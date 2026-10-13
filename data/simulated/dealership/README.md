# SIMULATED dealership data

Everything in this folder (except this README) is **generated** by `python/dealership_kpi_simulation.py` from the assumptions in `config/simulation.yaml`. The default seed is 20260930 and the period is 2026-02-01 to 2026-09-30.

- The data does **not** describe the performance of any real dealership, brand, salesperson or customer.
- Names and identifiers are synthetic: `DLR01`, `Salesperson DLR01-01`, `SIMVIN0000001`. The data contains no personal information.
- Every table carries `data_classification = SIMULATED`.
- Regenerate it, or change the seed, period or size, with:

```powershell
.\.venv\Scripts\python python\dealership_kpi_simulation.py --seed 7 --start 2026-01-01 --end 2026-06-30 --scale 0.5
```

The same seed and configuration produce byte-identical files.
