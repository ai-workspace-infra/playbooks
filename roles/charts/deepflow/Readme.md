
# 统计存储数据

select formatReadableSize(sum(rows)) as "每天写入行数", formatReadableSize(sum(bytes_on_disk)) as "每天落盘的字节", formatReadableSize(sum(data_uncompressed_bytes)) as "压缩前字节", sum(data_uncompressed_bytes)/sum(bytes_on_disk) as "压缩比",  sum(rows)/86400 as "平均每秒写入的行数" from cluster(df_cluster, system.parts) where partition like '%2024-12-03%' limit 10;


 可以grafana再 查下确认下，流日志的统计:
select  min(partition),max(partition),formatReadableSize(sum(rows)) as "每天写入行数", formatReadableSize(sum(bytes_on_disk)) as "每天落盘的字节", formatReadableSize(sum(data_uncompressed_bytes)) as "压缩前字节", sum(data_uncompressed_bytes)/sum(bytes_on_disk) as "压缩比",  sum(rows)/86400 as "平均每秒写入的行数" from cluster(df_cluster, system.parts) where partition like '%2024-12-03%' and table='l4_flow_log_local'  limit 10;

调用日志的统计:
select  min(partition),max(partition),formatReadableSize(sum(rows)) as "每天写入行数", formatReadableSize(sum(bytes_on_disk)) as "每天落盘的字节", formatReadableSize(sum(data_uncompressed_bytes)) as "压缩前字节", sum(data_uncompressed_bytes)/sum(bytes_on_disk) as "压缩比",  sum(rows)/86400 as "平均每秒写入的行数" from cluster(df_cluster, system.parts) where partition like '%2024-12-03%' and table='l7_flow_log_local'  limit 10;

