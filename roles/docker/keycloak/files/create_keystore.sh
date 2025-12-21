#!/bin/bash

# 定义非空检查函数
check_non_empty() {
  if [ -z "$1" ]; then
    echo "ERROR: $2 is not set."
    exit 1
  fi
}

# 使用非空检查函数检查所有变量
check_non_empty "$KEYSTORE_FILE" "KEYSTORE_FILE"
check_non_empty "$TRUSTSTORE_FILE" "TRUSTSTORE_FILE"
check_non_empty "$KEYSTORE_PASSWORD" "KEYSTORE_PASSWORD"
check_non_empty "$TRUSTSTORE_PASSWORD" "TRUSTSTORE_PASSWORD"
check_non_empty "$KEY_ALIAS" "KEY_ALIAS"
check_non_empty "$KEY_PASSWORD" "KEY_PASSWORD"
check_non_empty "$ROOT_CA_CERT" "ROOT_CA_CERT"

# 1. 创建 Keystore (包括私钥)
echo "Creating keystore..."
keytool -genkeypair -v -keystore "$KEYSTORE_FILE" -keyalg RSA -keysize 2048 -validity 365 -alias "$KEY_ALIAS" -storepass "$KEYSTORE_PASSWORD" -keypass "$KEY_PASSWORD" -dname "CN=localhost, OU=Dev, O=MyCompany, L=City, ST=State, C=US" -noprompt

# 2. 创建 Truststore 并导入根证书
echo "Creating truststore and importing root CA certificate..."
keytool -import -file "$ROOT_CA_CERT" -keystore "$TRUSTSTORE_FILE" -alias root-ca -storepass "$TRUSTSTORE_PASSWORD" -noprompt

echo "Keystore and truststore have been created and configured successfully."

# 创建 Diffie-Hellman 参数
#echo "Generating Diffie-Hellman parameters..."
#openssl dhparam -out /etc/ssl/dhparam.pem 2048
