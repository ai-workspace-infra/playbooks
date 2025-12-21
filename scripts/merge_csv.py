import sys
import glob
import pandas as pd

def merge_csv_files(src_pattern, dest_file):
    # 获取匹配的源 CSV 文件列表
    csv_files = glob.glob(src_pattern)

    if not csv_files:
        print(f"没有找到匹配的文件: {src_pattern}")
        return

    print(f"找到以下文件: {csv_files}")

    # 使用 pandas 读取所有 CSV 文件并合并
    combined_df = pd.concat([pd.read_csv(file) for file in csv_files], ignore_index=True)

    # 将合并后的数据写入目标 CSV 文件
    combined_df.to_csv(dest_file, index=False)
    print(f"合并完成，结果已保存到 {dest_file}")

if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("使用方法: python merge_csv.py <源文件模式> <目标文件>")
        sys.exit(1)

    src_pattern = sys.argv[1]
    dest_file = sys.argv[2]

    merge_csv_files(src_pattern, dest_file)
