import dlt
from pyspark.sql import functions as F

SOURCE_PATH = "abfss://learningcontainer@stockageapprentissage26.dfs.core.windows.net/raw/customers"

@dlt.table(
    name="bronze_customers",
    comment="Bronze streaming ingestion from ADLS Gen2 via Auto Loader"
)
def bronze_customers():
    return (
        spark.readStream.format("cloudFiles")
        .option("cloudFiles.format", "json")
        .option("cloudFiles.inferColumnTypes", "true")
        .load(SOURCE_PATH)
        .withColumn("_ingest_ts", F.current_timestamp())
        .withColumn("_source_file", F.col("_metadata.file_path"))

    )


# --- Silver staging with quality expectations ---
@dlt.table(
    name="silver_customers_staging",
    comment="Silver staging table with quality checks before CDC merge"
)
@dlt.expect("valid_customer_id", "customer_id IS NOT NULL")
@dlt.expect("valid_email_format", "email IS NULL OR email RLIKE '^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\\.[A-Za-z]{2,}$'")
@dlt.expect_or_drop("valid_operation", "op IN ('I','U','D')")
@dlt.expect_or_drop("valid_event_ts", "event_ts IS NOT NULL")
def silver_customers_staging():
    return (
        dlt.read_stream("bronze_customers")
        .select(
            F.col("customer_id").cast("string"),
            F.col("first_name").cast("string"),
            F.col("last_name").cast("string"),
            F.col("email").cast("string"),
            F.to_timestamp("event_ts").alias("event_ts"),
            F.col("op").cast("string"),
            F.col("_ingest_ts"),
            F.col("_source_file"),
        )
    )
# Target table for CDC SCD Type 1
dlt.create_target_table(
    name="silver_customers",
    comment="Current-state customer table maintained with CDC SCD Type 1"
)
dlt.apply_changes(
    target="silver_customers",
    source="silver_customers_staging",
    keys=["customer_id"],
    sequence_by=F.col("event_ts"),
    apply_as_deletes=F.expr("op = 'D'"),
    except_column_list=["op", "_source_file", "_ingest_ts"],
    stored_as_scd_type=2,
)


@dlt.table(
    name="gold_customer_domain_metrics",
    comment="Gold aggregated metrics by email domain",
    table_properties={
        "quality": "gold",
        "delta.autoOptimize.optimizeWrite": "true",
        "delta.autoOptimize.autoCompact": "true",
    },
    cluster_by=["email_domain"],
)
def gold_customer_domain_metrics():
    return (
        dlt.read("silver_customers")
        .where("email IS NOT NULL")
        .withColumn("email_domain", F.lower(F.split(F.col("email"), "@").getItem(1)))
        .groupBy("email_domain")
        .agg(
            F.countDistinct("customer_id").alias("customers_count"),
            F.max("event_ts").alias("last_event_ts"),
        )
    )