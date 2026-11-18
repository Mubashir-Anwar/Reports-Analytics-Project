"""Analytics (Phase 4): datasets, a data model, visuals and dashboards.

    datasets       a data adapter + query (MongoDB or SQL, the same query languages as the designer) imported into a
                   Parquet file (importer.py), refreshed by hand, on a schedule or through the API (refresher.py)
    model          column types, labels and roles per dataset, and relationships between datasets (store.py), so one
                   visual can use fields of a MongoDB dataset and a SQL Server dataset together
    query engine   a visual's fields, values and filters turned into DuckDB SQL over the Parquet files (engine.py)
    dashboards     tiles with visuals, slicers and dashboard filters (store.py; drawn by the /analytics page)

Data rows are never stored in the system database: only definitions and status. The Parquet files are kept in
REPORTING_ANALYTICS_DIR (default <data directory>/analytics).
"""
