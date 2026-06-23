#!/usr/bin/env bash
set -euo pipefail

# Configurable endpoints and tokens
LITELLM_URL="${LITELLM_URL:-http://127.0.0.1:4000}"
LITELLM_TOKEN="${LITELLM_TOKEN:-}"
if [ -z "$LITELLM_TOKEN" ] && [ -f "$HOME/.ai_workspace_auth_token" ]; then
    LITELLM_TOKEN="$(cat "$HOME/.ai_workspace_auth_token")"
fi

if [ -z "$LITELLM_TOKEN" ]; then
    echo "Error: LITELLM_TOKEN is not set and could not be read from ~/.ai_workspace_auth_token."
    exit 1
fi

if [ -z "${DEEPSEEK_API_KEY:-}" ] && [ -z "${NVIDIA_API_KEY:-}" ] && [ -z "${OLLAMA_API_KEY:-}" ]; then
    echo "[INFO] DEEPSEEK_API_KEY, NVIDIA_API_KEY, and OLLAMA_API_KEY are empty. Manual configuration mode."
    exit 0
fi

echo "[INFO] Using LiteLLM URL: $LITELLM_URL"

# Function to add a model
add_model() {
    local alias_name="$1"
    local litellm_provider_model="$2"
    local api_key_env_var="$3"
    local api_base="${4:-}"

    # Skip registration when the backing API key was not provided (empty env var).
    if [ -z "${!api_key_env_var:-}" ]; then
        echo "[SKIP] $alias_name: $api_key_env_var is empty; not registering."
        return 0
    fi

    echo "Adding model: $alias_name -> $litellm_provider_model"

    local payload
    if [ -n "$api_base" ]; then
        payload=$(cat <<EOF
{
  "model_name": "$alias_name",
  "litellm_params": {
    "model": "$litellm_provider_model",
    "api_key": "os.environ/$api_key_env_var",
    "api_base": "$api_base"
  },
  "model_info": {
    "id": "$alias_name",
    "mode": "chat"
  }
}
EOF
)
    else
        payload=$(cat <<EOF
{
  "model_name": "$alias_name",
  "litellm_params": {
    "model": "$litellm_provider_model",
    "api_key": "os.environ/$api_key_env_var"
  },
  "model_info": {
    "id": "$alias_name",
    "mode": "chat"
  }
}
EOF
)
    fi

    local response
    local http_code
    response=$(curl -s -w "\nHTTP_CODE:%{http_code}" -X POST "$LITELLM_URL/model/new" \
        -H "Authorization: Bearer $LITELLM_TOKEN" \
        -H "Content-Type: application/json" \
        -d "$payload") || true
    
    http_code=$(echo "$response" | grep -Eo 'HTTP_CODE:[0-9]{3}' | cut -d':' -f2 || echo "000")
    if [ "$http_code" = "200" ] || [ "$http_code" = "201" ]; then
        echo "[SUCCESS] Model $alias_name added."
    else
        echo "[ERROR] Failed to add model $alias_name. HTTP Code: $http_code"
        echo "Response: $response"
    fi
}

if [ -n "${DEEPSEEK_API_KEY:-}" ]; then
    echo "========================================="
    echo "Registering DeepSeek Models..."
    echo "========================================="
    add_model "deepseek-v4-flash" "deepseek/deepseek-v4-flash" "DEEPSEEK_API_KEY"
    add_model "deepseek-v4-pro" "deepseek/deepseek-v4-pro" "DEEPSEEK_API_KEY"
    add_model "deepseek-chat" "deepseek/deepseek-chat" "DEEPSEEK_API_KEY"
    add_model "deepseek-reasoner" "deepseek/deepseek-reasoner" "DEEPSEEK_API_KEY"
fi

if [ -n "${NVIDIA_API_KEY:-}" ]; then
    echo "========================================="
    echo "Registering NVIDIA Build Models..."
    echo "========================================="
    add_model "nvidia/glm-5.2" "openai/thudm/glm-5.2-chat" "NVIDIA_API_KEY" "https://integrate.api.nvidia.com/v1"
    add_model "nvidia/minimax-m3" "openai/minimax/minimax-m3" "NVIDIA_API_KEY" "https://integrate.api.nvidia.com/v1"
    add_model "nvidia/qwen3.5" "openai/alibaba/qwen3.5-72b-instruct" "NVIDIA_API_KEY" "https://integrate.api.nvidia.com/v1"
fi

echo "========================================="
echo "Registering Gemini Models..."
echo "========================================="
if [ -n "${GEMINI_API_KEY:-}" ]; then
    add_model "gemini-2.5-pro" "gemini/gemini-2.5-pro" "GEMINI_API_KEY"
    add_model "gemini-2.5-flash" "gemini/gemini-2.5-flash" "GEMINI_API_KEY"
    add_model "gemini-1.5-pro" "gemini/gemini-1.5-pro" "GEMINI_API_KEY"
fi

echo "========================================="
echo "Registering GPT Models..."
echo "========================================="
if [ -n "${OPENAI_API_KEY:-}" ]; then
    add_model "gpt-5.5" "openai/gpt-5.5" "OPENAI_API_KEY"
    add_model "gpt-5.4" "openai/gpt-5.4" "OPENAI_API_KEY"
    add_model "gpt-5.4-mini" "openai/gpt-5.4-mini" "OPENAI_API_KEY"
fi

echo "========================================="
echo "Registering Claude Models..."
echo "========================================="
if [ -n "${ANTHROPIC_API_KEY:-}" ]; then
    add_model "claude-3.5-sonnet" "anthropic/claude-3-5-sonnet-20241022" "ANTHROPIC_API_KEY"
    add_model "claude-3.5-haiku" "anthropic/claude-3-5-haiku-20241022" "ANTHROPIC_API_KEY"
    add_model "claude-3-opus" "anthropic/claude-3-opus-20240229" "ANTHROPIC_API_KEY"
fi

if [ -n "${OLLAMA_API_KEY:-}" ]; then
    echo "========================================="
    echo "Registering OLLAMA Cloud Models..."
    echo "========================================="
    OLLAMA_API_BASE="${OLLAMA_API_BASE:-https://api.ollama.cloud/v1}"
    add_model "ollama-cloud/kimi-k2.7-code" "openai/moonshot/kimi-k2.7-code" "OLLAMA_API_KEY" "$OLLAMA_API_BASE"
fi

echo "All models requested have been registered."
echo "You can check them at $LITELLM_URL/ui/?page=models"
