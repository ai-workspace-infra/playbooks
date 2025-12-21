#!/bin/bash

# 获取参数
DOMAIN="$1"
VALID_DAYS="$2"
OUTPUT_DIR="$3"

# 确保参数不为空
if [[ -z "$DOMAIN" || -z "$VALID_DAYS" || -z "$OUTPUT_DIR" ]]; then
  echo "Usage: $0 <domain_name> <valid_days> <output_dir>"
  exit 1
fi

# 确保输出目录存在
mkdir -p "$OUTPUT_DIR"

CERT_FILE="$DOMAIN.cert"
KEY_FILE="$DOMAIN.key"

echo "Generating self-signed SSL certificate for domain: $DOMAIN (with SAN), valid for $VALID_DAYS days"

# 1. 生成 CA 私钥
openssl genrsa -out "$OUTPUT_DIR/ca.key" 2048

# 2. 生成 CA 证书（自签的根证书）
openssl req -x509 -new -nodes \
  -key "$OUTPUT_DIR/ca.key" \
  -sha256 -days "$VALID_DAYS" \
  -out "$OUTPUT_DIR/ca.cert" \
  -subj "/C=CN/ST=State/L=City/O=Company/OU=Org/CN=Custom-CA"

# 3. 生成服务器私钥
openssl genrsa -out "$OUTPUT_DIR/$KEY_FILE" 2048

# 4. 创建 OpenSSL 配置文件（兼容 Linux & macOS）
SAN_CONFIG="$OUTPUT_DIR/san.cnf"
cat <<EOF > "$SAN_CONFIG"
[ req ]
default_bits       = 2048
prompt             = no
default_md         = sha256
req_extensions     = req_ext
distinguished_name = dn

[ dn ]
C  = CN
ST = State
L  = City
O  = Company
OU = Org
CN = $DOMAIN

[ req_ext ]
subjectAltName = @alt_names

[ alt_names ]
DNS.1 = $DOMAIN
EOF

# 5. 生成 CSR（证书签名请求）
openssl req -new -key "$OUTPUT_DIR/$KEY_FILE" \
  -out "$OUTPUT_DIR/$DOMAIN.csr" \
  -config "$SAN_CONFIG"

# 6. 用 CA 证书签发服务器证书，保留 SAN
openssl x509 -req \
  -in "$OUTPUT_DIR/$DOMAIN.csr" \
  -CA "$OUTPUT_DIR/ca.cert" \
  -CAkey "$OUTPUT_DIR/ca.key" \
  -CAcreateserial \
  -out "$OUTPUT_DIR/$CERT_FILE" \
  -days "$VALID_DAYS" \
  -sha256 \
  -extensions req_ext -extfile "$SAN_CONFIG"

# 7. 清理 CSR 和配置文件
rm -f "$OUTPUT_DIR/$DOMAIN.csr" "$SAN_CONFIG"

echo "✅ Self-signed SSL certificate (with SAN) for $DOMAIN generated in $OUTPUT_DIR!"
