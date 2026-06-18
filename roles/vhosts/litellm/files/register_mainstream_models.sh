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

echo "[INFO] Using LiteLLM URL: $LITELLM_URL"

# Function to add a model
add_model() {
    local alias_name="$1"
    local litellm_provider_model="$2"
    local api_key_env_var="$3"
    local api_base="${4:-}"
    
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
        -d "$payload")
    
    http_code=$(echo "$response" | grep -Eo 'HTTP_CODE:[0-9]{3}' | cut -d':' -f2 || echo "000")
    if [ "$http_code" = "200" ] || [ "$http_code" = "201" ]; then
        echo "[SUCCESS] Model $alias_name added."
    else
        echo "[ERROR] Failed to add model $alias_name. HTTP Code: $http_code"
        echo "Response: $response"
    fi
}

echo "========================================="
echo "Registering DeepSeek Models..."
echo "========================================="
add_model "deepseek-chat" "deepseek/deepseek-chat" "DEEPSEEK_API_KEY"
add_model "deepseek-reasoner" "deepseek/deepseek-reasoner" "DEEPSEEK_API_KEY"
add_model "deepseek-v4-flash" "deepseek/deepseek-v4-flash" "DEEPSEEK_API_KEY"
add_model "deepseek-v4-pro" "deepseek/deepseek-v4-pro" "DEEPSEEK_API_KEY"

echo "========================================="
echo "Registering NVIDIA Build Models..."
echo "========================================="
# For NVIDIA NIM models, you can use openai format with custom base, or nvidia_nim/ provider
add_model "nvidia/deepseek-r1" "openai/deepseek-ai/deepseek-r1" "NVIDIA_API_KEY" "https://integrate.api.nvidia.com/v1"
add_model "nvidia/minimax-text-01" "openai/minimax/minimax-text-01" "NVIDIA_API_KEY" "https://integrate.api.nvidia.com/v1"
add_model "nvidia/glm-4" "openai/thudm/glm-4-9b-chat" "NVIDIA_API_KEY" "https://integrate.api.nvidia.com/v1"
add_model "nvidia/glm-5" "openai/thudm/glm-5" "NVIDIA_API_KEY" "https://integrate.api.nvidia.com/v1"

echo "========================================="
echo "Registering Gemini Models..."
echo "========================================="
add_model "gemini-2.5-pro" "gemini/gemini-2.5-pro" "GEMINI_API_KEY"
add_model "gemini-2.5-flash" "gemini/gemini-2.5-flash" "GEMINI_API_KEY"
add_model "gemini-1.5-pro" "gemini/gemini-1.5-pro" "GEMINI_API_KEY"

echo "========================================="
echo "Registering GPT Models..."
echo "========================================="
add_model "gpt-5.5" "openai/gpt-5.5" "OPENAI_API_KEY"
add_model "gpt-5.4" "openai/gpt-5.4" "OPENAI_API_KEY"
add_model "gpt-5.4-mini" "openai/gpt-5.4-mini" "OPENAI_API_KEY"

echo "========================================="
echo "Registering Claude Models..."
echo "========================================="
add_model "claude-3.5-sonnet" "anthropic/claude-3-5-sonnet-20241022" "ANTHROPIC_API_KEY"
add_model "claude-3.5-haiku" "anthropic/claude-3-5-haiku-20241022" "ANTHROPIC_API_KEY"
add_model "claude-3-opus" "anthropic/claude-3-opus-20240229" "ANTHROPIC_API_KEY"

echo "========================================="
echo "Registering Zhipu (GLM) using OLLAMA_API_KEY..."
echo "========================================="
add_model "glm-4" "openai/glm-4" "OLLAMA_API_KEY" "https://open.bigmodel.cn/api/paas/v4"
add_model "glm-5" "openai/glm-5" "OLLAMA_API_KEY" "https://open.bigmodel.cn/api/paas/v4"

echo "========================================="
echo "Registering OLLAMA Cloud Models..."
echo "========================================="
# Assuming OLLAMA API is exposed via a cloud endpoint or an OpenAI proxy
OLLAMA_API_BASE="${OLLAMA_API_BASE:-https://api.ollama.cloud/v1}"
add_model "ollama-llama3" "openai/llama3" "OLLAMA_API_KEY" "$OLLAMA_API_BASE"
add_model "ollama-qwen" "openai/qwen" "OLLAMA_API_KEY" "$OLLAMA_API_BASE"

echo "All models requested have been registered."
echo "You can check them at $LITELLM_URL/ui/?page=models"
