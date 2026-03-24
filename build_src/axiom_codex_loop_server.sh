#!/bin/bash
# Axiom Codex Loop — runs directly on the server
# 15 steps per cycle (0-14): 0-2 sequential, 3-6 bridge/dedup/rating/journal, 7-10 parallel research+deploy, 11-12 sequential, 13 cleanup, 14 exp descriptions
# Validate step removed (credit cron handles it, meta-watchdog monitors)
# Dedup step mechanized (Python script, no LLM)

MODEL="gpt-5.4"
EFFORT="medium"
PDIR="/opt/axiom/experiment_session/prompts"
LOG="/opt/axiom/experiment_session/logs/codex_loop.log"
STATUS="/opt/axiom/experiment_session/logs/codex_status.txt"
HISTORY="/opt/axiom/experiment_session/logs/cycle_history.txt"
MAX_CYCLES=16
WAIT_SECONDS=6480  # 1.8 hours
STEP_TIMEOUT=7200  # 2 hours max per step

LOCKFILE="/tmp/axiom_codex_loop.lock"
if [ -f "$LOCKFILE" ]; then
    pid=$(cat "$LOCKFILE")
    if kill -0 "$pid" 2>/dev/null; then
        echo "[$(date -u)] Already running (PID $pid), exiting" >> "$LOG"
        exit 0
    fi
fi
echo $$ > "$LOCKFILE"
trap 'rm -f "$LOCKFILE"' EXIT

STEPS=(
    "0:Security:axiom_security_prompt.txt"
    "1:Health Check:axiom_health_prompt.txt"
    "2:Science:axiom_science_prompt.txt"
    "7:CPU Research:axiom_cpu_research_prompt.txt"
    "8:CPU Dry-Run:axiom_cpu_dryrun_prompt.txt"
    "9:GPU Research:axiom_gpu_research_prompt.txt"
    "10:GPU Dry-Run:axiom_gpu_dryrun_prompt.txt"
    "11:Error Triage 1:axiom_perf_audit_prompt.txt"
    "12:Error Triage 2:axiom_error_triage_prompt.txt"
    "14:Exp Descriptions:axiom_experiment_descriptions_prompt.txt"
)

log() {
    local msg="[$(date -u '+%Y-%m-%d %H:%M:%S')] $1"
    echo "$msg" >> "$LOG"
}

update_status() {
    echo "$1" > "$STATUS"
    echo "$(date -u '+%Y-%m-%d %H:%M:%S') | $1" >> "${STATUS}.history"
}

count_recent_cycles() {
    local cutoff=$(( $(date +%s) - 86400 ))
    if [ ! -f "$HISTORY" ]; then
        echo 0
        return
    fi
    awk -v cutoff="$cutoff" '$1 > cutoff { count++ } END { print count+0 }' "$HISTORY"
}

prune_history() {
    local cutoff=$(( $(date +%s) - 86400 ))
    if [ -f "$HISTORY" ]; then
        awk -v cutoff="$cutoff" '$1 > cutoff' "$HISTORY" > "${HISTORY}.tmp"
        mv "${HISTORY}.tmp" "$HISTORY"
    fi
}

run_step() {
    local num="$1"
    local name="$2"
    local prompt="$3"
    local step_start=$(date +%s)

    update_status "Cycle $CYCLE_NUM/$MAX_CYCLES | Step $num/14: $name | Started $(date -u '+%H:%M:%S')"
    log "  [Step $num/14] $name — starting"

    # Run codex exec with timeout
    timeout "$STEP_TIMEOUT" codex exec \
        -m "$MODEL" \
        -c model_reasoning_effort="$EFFORT" \
        --dangerously-bypass-approvals-and-sandbox \
        "Read ${PDIR}/${prompt} and execute the instructions in it directly. You ARE the agent described in the prompt. Run all shell commands and SQL queries as instructed." \
        >> "$LOG" 2>&1

    local exit_code=$?
    local step_end=$(date +%s)
    local elapsed=$(( step_end - step_start ))
    local mins=$(( elapsed / 60 ))
    local secs=$(( elapsed % 60 ))

    if [ $exit_code -eq 124 ]; then
        log "  [Step $num/14] $name — TIMEOUT after ${mins}m ${secs}s"
    else
        log "  [Step $num/14] $name — done in ${mins}m ${secs}s (exit $exit_code)"
    fi
}

# Main loop
while true; do
    # Check sliding window
    RECENT=$(count_recent_cycles)
    if [ "$RECENT" -ge "$MAX_CYCLES" ]; then
        update_status "PAUSED | $RECENT/$MAX_CYCLES cycles in last 24h | Waiting 1m"
        log "Limit reached: $RECENT/$MAX_CYCLES cycles in last 24h. Waiting 30 min..."
        # Notify once when first hitting the limit
        if [ ! -f /tmp/axiom_pause_notified.flag ]; then
            OLDEST=$(head -1 "$HISTORY")
            RESUME=$(date -u -d "@$(( OLDEST + 86400 ))" '+%H:%M UTC')
            curl -s --connect-timeout 3 --max-time 5 -X POST "http://localhost:9080/message?token=REDACTED_GOTIFY_TOKEN" \
                -F "title=Axiom Loop Paused" \
                -F "message=Rate limit hit: $RECENT/$MAX_CYCLES cycles in 24h. Waiting for oldest cycle to age out. Resumes ~$RESUME." \
                -F "priority=5" > /dev/null 2>&1
            touch /tmp/axiom_pause_notified.flag
        fi
        sleep 60
        continue
    else
        rm -f /tmp/axiom_pause_notified.flag
    fi

    # Record cycle start
    CYCLE_START=$(date +%s)
    echo "$CYCLE_START" >> "$HISTORY"
    CYCLE_NUM=$(( RECENT + 1 ))

    log "========================================="
    log "  Cycle $CYCLE_NUM/$MAX_CYCLES — $(date -u '+%Y-%m-%d %H:%M:%S')"
    log "========================================="

    # Run steps 0-2 (Security, Health, Science)
    for step_entry in "${STEPS[@]}"; do
        IFS=':' read -r num name prompt <<< "$step_entry"
        if [ "$num" -ge 7 ]; then
            continue
        fi
        run_step "$num" "$name" "$prompt"
    done

    # Steps 3-6: Bridge, Dedup, Rating, Journal (hardcoded sequential)

    # Bridge: convert findings_summary.txt -> results file for dedup consumption
    log "  [Step 3/14] Bridge — converting science findings to results format"
    update_status "Cycle $CYCLE_NUM/$MAX_CYCLES | Step 3/14: Bridge | Started $(date -u '+%H:%M:%S')"
    python3 /opt/axiom/experiment_session/science_to_results.py >> "$LOG" 2>&1

    # Dedup (mechanical — no LLM tokens)
    log "  [Step 4/14] Dedup — deduplicating findings"
    update_status "Cycle $CYCLE_NUM/$MAX_CYCLES | Step 4/14: Dedup | Started $(date -u '+%H:%M:%S')"
    DEDUP_START=$(date +%s)
    python3 /opt/axiom/axiom_dedup_mechanical.py >> "$LOG" 2>&1
    DEDUP_EXIT=$?
    DEDUP_ELAPSED=$(( $(date +%s) - DEDUP_START ))
    log "  [Step 4/14] Dedup — done in ${DEDUP_ELAPSED}s (exit $DEDUP_EXIT)"

    # Rating step — reads freshly deduped findings
    update_status "Cycle $CYCLE_NUM/$MAX_CYCLES | Step 5/14: Rating | Started $(date -u '+%H:%M:%S')"
    log "  [Step 5/14] Rating — significance scoring"
    RATING_START=$(date +%s)
    timeout 1800 codex exec -m gpt-5.4 -c model_reasoning_effort="medium" \
        --dangerously-bypass-approvals-and-sandbox \
        --skip-git-repo-check -C /opt/axiom \
        "Read /opt/axiom/experiment_session/prompts/axiom_rating_prompt.txt and execute the instructions in it directly. You ARE the agent described in the prompt. Run all shell commands as instructed." \
        >> "$LOG" 2>&1
    RATING_EXIT=$?
    RATING_ELAPSED=$(( $(date +%s) - RATING_START ))
    RATING_MIN=$(( RATING_ELAPSED / 60 ))
    RATING_SEC=$(( RATING_ELAPSED % 60 ))
    log "  [Step 5/14] Rating — done in ${RATING_MIN}m ${RATING_SEC}s (exit $RATING_EXIT)"

    # Science Journal — long-term memory (runs after rating, before research)
    update_status "Cycle $CYCLE_NUM/$MAX_CYCLES | Step 6/14: Journal | Started $(date -u '+%H:%M:%S')"
    log "  [Step 6/14] Journal — writing science memory entry"
    JOURNAL_START=$(date +%s)
    timeout 600 codex exec \
        -m gpt-5.4 -c model_reasoning_effort="medium" \
        --dangerously-bypass-approvals-and-sandbox \
        "Read ${PDIR}/axiom_science_journal_prompt.txt and execute the instructions in it directly. You ARE the agent described in the prompt. Run all shell commands as instructed." \
        >> "$LOG" 2>&1
    JOURNAL_EXIT=$?
    JOURNAL_ELAPSED=$(( $(date +%s) - JOURNAL_START ))
    JOURNAL_MIN=$(( JOURNAL_ELAPSED / 60 ))
    JOURNAL_SEC=$(( JOURNAL_ELAPSED % 60 ))
    log "  [Step 6/14] Journal — done in ${JOURNAL_MIN}m ${JOURNAL_SEC}s (exit $JOURNAL_EXIT)"

    # Run CPU and GPU pipelines in PARALLEL
    # CPU pipeline: Research (7) -> Dry-Run (8)
    # GPU pipeline: Research (9) -> Dry-Run (10)
    # Both must complete before Error Triage (11)

    log "  Starting parallel pipelines: CPU (7->8) + GPU (9->10)"

    (
        run_step "7" "CPU Research" "axiom_cpu_research_prompt.txt"
        run_step "8" "CPU Dry-Run" "axiom_cpu_dryrun_prompt.txt"
    ) &
    CPU_PID=$!

    (
        log "  [GPU subshell] Starting GPU pipeline"
        curl -s --connect-timeout 3 --max-time 5 -X POST "http://localhost:9080/message?token=REDACTED_GOTIFY_TOKEN" -F "title=GPU Pipeline Starting" -F "message=Step 9 (GPU Research) starting now. Get off VRChat!" -F "priority=7" > /dev/null 2>&1
        log "  [GPU subshell] About to run GPU Research"
        run_step "9" "GPU Research" "axiom_gpu_research_prompt.txt"
        GPU_RESEARCH_EXIT=$?
        log "  [GPU subshell] GPU Research exited with code $GPU_RESEARCH_EXIT"
        if [ $GPU_RESEARCH_EXIT -ne 0 ] && [ $GPU_RESEARCH_EXIT -ne 124 ]; then
            log "  [GPU subshell] GPU Research FAILED (exit $GPU_RESEARCH_EXIT), skipping Dry-Run"
        else
            log "  [GPU subshell] About to run GPU Dry-Run"
            run_step "10" "GPU Dry-Run" "axiom_gpu_dryrun_prompt.txt"
            log "  [GPU subshell] GPU Dry-Run exited with code $?"
        fi
        curl -s --connect-timeout 3 --max-time 5 -X POST "http://localhost:9080/message?token=REDACTED_GOTIFY_TOKEN" -F "title=GPU Pipeline Done" -F "message=Step 10 (GPU Dry-Run) finished. VRChat is safe." -F "priority=5" > /dev/null 2>&1
        log "  [GPU subshell] GPU pipeline complete"
    ) &
    GPU_PID=$!

    # Wait for both pipelines with timeout (max 2 hours per pipeline)
    PIPELINE_TIMEOUT=7200

    # Background watchdog: kill pipelines if they exceed timeout
    (
        sleep $PIPELINE_TIMEOUT
        if kill -0 $CPU_PID 2>/dev/null; then
            log "  WARNING: CPU pipeline timed out after ${PIPELINE_TIMEOUT}s, killing"
            kill $CPU_PID 2>/dev/null
            sleep 2
            kill -9 $CPU_PID 2>/dev/null
        fi
        if kill -0 $GPU_PID 2>/dev/null; then
            log "  WARNING: GPU pipeline timed out after ${PIPELINE_TIMEOUT}s, killing"
            kill $GPU_PID 2>/dev/null
            sleep 2
            kill -9 $GPU_PID 2>/dev/null
        fi
    ) &
    WATCHDOG_PID=$!

    wait $CPU_PID
    CPU_EXIT=$?
    wait $GPU_PID
    GPU_EXIT=$?

    # Kill watchdog since pipelines finished normally
    kill $WATCHDOG_PID 2>/dev/null
    wait $WATCHDOG_PID 2>/dev/null

    log "  Parallel pipelines complete (CPU exit=$CPU_EXIT, GPU exit=$GPU_EXIT)"

    # Sequential: Error Triage 1, Error Triage 2
    run_step "11" "Error Triage 1" "axiom_perf_audit_prompt.txt"
    run_step "12" "Error Triage 2" "axiom_error_triage_prompt.txt"

    # Mechanical cleanup — runs retired experiment cleanup (no LLM tokens)
    log "  [Step 13/14] Cleanup — processing retirements"
    update_status "Cycle $CYCLE_NUM/$MAX_CYCLES | Step 13/14: Cleanup | Started $(date -u '+%H:%M:%S')"
    CLEANUP_START=$(date +%s)
    python3 /opt/axiom/axiom_cleanup_cron.py >> "$LOG" 2>&1
    CLEANUP_ELAPSED=$(( $(date +%s) - CLEANUP_START ))
    log "  [Step 13/14] Cleanup — done in ${CLEANUP_ELAPSED}s"

    run_step "14" "Exp Descriptions" "axiom_experiment_descriptions_prompt.txt"

    # Cycle complete
    CYCLE_END=$(date +%s)
    CYCLE_ELAPSED=$(( CYCLE_END - CYCLE_START ))
    CYCLE_MINS=$(( CYCLE_ELAPSED / 60 ))
    log "  Cycle $CYCLE_NUM complete in ${CYCLE_MINS}m"

    # Prune old history
    prune_history

    # Cooldown
    update_status "WAITING | Cycle $CYCLE_NUM done | Next in 1.8h (~$(date -u -d "+${WAIT_SECONDS} seconds" '+%H:%M') UTC)"
    log "  Waiting ${WAIT_SECONDS}s (1.8h) before next cycle..."
    sleep "$WAIT_SECONDS"
done
