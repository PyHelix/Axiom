# Axiom: Price-Per-FLOP Credit System

**Author:** PyHelix
**Date:** March 18-24, 2026
**Project:** Axiom BOINC — https://axiom.heliex.net
**Status:** Live in production

---

## Overview

Axiom uses a market-rate credit system where volunteers earn credit proportional to the economic value of the compute they donate, not just raw FLOPS.

## How Credit Is Calculated

Credit is awarded every 5 minutes by an automated cron job:

```
credit = elapsed_time * p_fpops * CPU_SCALE + elapsed_time * p_gpu_fpops * GPU_SCALE
```

Where:
- `elapsed_time` — actual wall-clock time the task ran
- `p_fpops` — host's CPU benchmark (FLOPS)
- `p_gpu_fpops` — host's GPU benchmark (FLOPS)
- `CPU_SCALE` / `GPU_SCALE` — market-rate scaling factors based on real hardware prices, updated hourly

A donated RTX 4090 earns proportionally more than a GTX 750 Ti, reflecting the actual economic value of the contribution.

## Credit Integrity

- User total credit uses `GREATEST(current, SUM(hosts))` to preserve manually-added legacy credit
- Host and user credit are synced independently to prevent drift
- Daily automated backups of all credit data
- RAC (Recent Average Credit) recalculated every 30 minutes based on 7-day rolling window

## Price Updates

GPU and CPU price-per-FLOP values are refreshed hourly from hardware market data. This ensures credit stays fair as hardware prices change over time.

---

## Implementation Date

- Conceived and deployed: March 18, 2026
- Credit cron runs every 5 minutes
- Price updater runs hourly
