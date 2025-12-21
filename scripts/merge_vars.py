
import os
import sys
import yaml
import json
from secret.hcp import secret

def check_env_vars(vars):
    """检查环境变量是否存在并且非空"""
    for var in vars:
        value = os.environ.get(var)
        if value is None or value == "":
            print(f"Error: Environment variable '{var}' is not set or is empty.")
            sys.exit(1)

def main():
    # 定义需要检查的环境变量
    required_vars = [
        "DOMAIN",
        "CLUSTER_NAME",
        "SUDO_PASSWORD",
        "HCP_API_URL",
        "HCP_CLIENT_ID",
        "HCP_CLIENT_SECRET",
        "GATEWAY_PUBLIC_CONFIG"
    ]
    
    # 检查环境变量
    check_env_vars(required_vars)

    # 从环境变量获取输入
    domain = os.environ.get("DOMAIN")
    cluster_name = os.environ.get("CLUSTER_NAME")
    ansible_become_pass = os.environ.get("SUDO_PASSWORD")
    hcp_api_url = os.environ.get("HCP_API_URL")
    hcp_client_id = os.environ.get("HCP_CLIENT_ID")
    hcp_client_secret = os.environ.get("HCP_CLIENT_SECRET")
    gateway_public_config = os.environ.get("GATEWAY_PUBLIC_CONFIG")

    # 检查并去掉开头的 '$'
    if gateway_public_config.startswith('$'):
        gateway_public_config = gateway_public_config[1:]

    # 获取 HCP API 令牌
    api_token = secret.get_hcp_api_token(hcp_client_id, hcp_client_secret)

    # 获取密钥数据
    secret_data = secret.get_secret_data(hcp_api_url, api_token)

    # 将 gateway_public_config 转换为字典
    public_config_dict = yaml.safe_load(gateway_public_config)

    # 从密钥数据中提取 private_key
    private_key_name = f"{public_config_dict.get('name', '')}_private_key"
    private_key = secret.get_secret_value_by_name(secret_data, private_key_name)

    if private_key is None:
        print(f"Error: Secret value for '{private_key_name}' not found.")
        sys.exit(1)

    # 填充 private_key
    public_config_dict['private_key'] = private_key

    # 填充 peers 部分的 public_key
    for peer in public_config_dict.get('peers', []):
        peer_name = peer.get('name', '')
        public_key_name = f"{peer_name}_public_key"
        public_key = secret.get_secret_value_by_name(secret_data, public_key_name)

        if public_key is None:
            print(f"Error: Secret value for '{public_key_name}' not found.")
            sys.exit(1)

        peer['public_key'] = public_key

    # 构建最终的配置字典
    final_config = {
        "domain": domain,
        "cluster_name": cluster_name,
        "ansible_become_pass": ansible_become_pass,
        "gateway": {
            "public_config": public_config_dict
        }
    }

    # 输出为 JSON
    with open("extra_vars.json", "w") as json_file:
        json.dump(final_config, json_file, indent=2)

if __name__ == "__main__":
    main()
