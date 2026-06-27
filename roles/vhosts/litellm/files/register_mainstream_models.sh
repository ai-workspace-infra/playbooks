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

# Aliases successfully registered, collected for the post-registration probe.
REGISTERED=()

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
    "api_key": "${!api_key_env_var}",
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
    "api_key": "${!api_key_env_var}"
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
        REGISTERED+=("$alias_name")
    else
        echo "[INFO] Model $alias_name failed to add via /model/new (HTTP $http_code), attempting /model/update..."
        response=$(curl -s -w "\nHTTP_CODE:%{http_code}" -X POST "$LITELLM_URL/model/update" \
            -H "Authorization: Bearer $LITELLM_TOKEN" \
            -H "Content-Type: application/json" \
            -d "$payload") || true
        http_code=$(echo "$response" | grep -Eo 'HTTP_CODE:[0-9]{3}' | cut -d':' -f2 || echo "000")
        if [ "$http_code" = "200" ] || [ "$http_code" = "201" ]; then
            echo "[SUCCESS] Model $alias_name updated."
            REGISTERED+=("$alias_name")
        else
            echo "[ERROR] Failed to add/update model $alias_name. HTTP Code: $http_code"
            echo "Response: $response"
        fi
    fi
}

# Probe a single registered alias by sending a real 1-token completion through
# LiteLLM. Registration (presence in /v1/models) only proves the row exists in
# the DB; it does NOT prove the upstream model id / api_base / entitlement are
# valid. This is the only check that proves an alias is actually callable.
# Echoes "PASS" / "FAIL <http> <reason>" and returns 0 only on PASS.
probe_model() {
    local alias_name="$1"
    local body http_code msg
    body=$(curl -s -m 60 -w "\nHTTP_CODE:%{http_code}" \
        -X POST "$LITELLM_URL/v1/chat/completions" \
        -H "Authorization: Bearer $LITELLM_TOKEN" \
        -H "Content-Type: application/json" \
        -d "{\"model\":\"$alias_name\",\"messages\":[{\"role\":\"user\",\"content\":\"ping\"}],\"max_tokens\":1}") || true
    http_code=$(echo "$body" | grep -Eo 'HTTP_CODE:[0-9]{3}' | cut -d':' -f2 || echo "000")
    if [ "$http_code" = "200" ]; then
        echo "PASS"
        return 0
    fi
    # Pull a short reason out of the error for the report. Prefer the upstream
    # provider message (e.g. "this model requires a subscription") over
    # LiteLLM's verbose fallback-wrapper text, then cap the length.
    local flat
    flat=$(echo "$body" | sed 's/HTTP_CODE:[0-9]*//' | tr '\n' ' ')
    msg=$(echo "$flat" | grep -Eo "'error': '[^']*'" | head -1 | sed "s/'error': '//; s/'$//")
    [ -z "$msg" ] && msg=$(echo "$flat" | grep -Eo '"message":"[^"]*"' | head -1 | cut -d'"' -f4)
    [ -z "$msg" ] && msg="$flat"
    echo "FAIL $http_code $(echo "${msg:-unknown}" | cut -c1-90)"
    return 1
}

if [ -n "${DEEPSEEK_API_KEY:-}" ]; then
    echo "========================================="
    echo "Registering DeepSeek Models..."
    echo "========================================="
    add_model "deepseek/deepseek-v4-flash" "deepseek/deepseek-v4-flash" "DEEPSEEK_API_KEY"
    add_model "deepseek/deepseek-v4-pro" "deepseek/deepseek-v4-pro" "DEEPSEEK_API_KEY"
    add_model "deepseek/deepseek-chat" "deepseek/deepseek-chat" "DEEPSEEK_API_KEY"
    add_model "deepseek/deepseek-reasoner" "deepseek/deepseek-reasoner" "DEEPSEEK_API_KEY"
fi

if [ -n "${NVIDIA_API_KEY:-}" ]; then
    echo "========================================="
    echo "Registering NVIDIA Build Models..."
    echo "========================================="
    # NVIDIA NIM model ids are vendor-namespaced (deepseek-ai/..., minimaxai/...,
    # qwen/..., z-ai/..., moonshotai/...); bare names 404 on the upstream router.
    # Every alias below maps to a model that EXISTS in the live GET /v1/models
    # catalog. NVIDIA serves glm-5.1 and kimi-k2.6 (no 5.2 / k2.7), so the
    # aliases are named for the real versions rather than lying about them.
    NVIDIA_API_BASE="${NVIDIA_API_BASE:-https://integrate.api.nvidia.com/v1}"
    add_model "nvidia/deepseek-v4-flash" "openai/deepseek-ai/deepseek-v4-flash" "NVIDIA_API_KEY" "$NVIDIA_API_BASE"
    add_model "nvidia/deepseek-v4-pro" "openai/deepseek-ai/deepseek-v4-pro" "NVIDIA_API_KEY" "$NVIDIA_API_BASE"
    add_model "nvidia/glm-5.1" "openai/z-ai/glm-5.1" "NVIDIA_API_KEY" "$NVIDIA_API_BASE"
    add_model "nvidia/minimax-m3" "openai/minimaxai/minimax-m3" "NVIDIA_API_KEY" "$NVIDIA_API_BASE"
    add_model "nvidia/qwen3.5" "openai/qwen/qwen3.5-397b-a17b" "NVIDIA_API_KEY" "$NVIDIA_API_BASE"
    add_model "nvidia/kimi-k2.6" "openai/moonshotai/kimi-k2.6" "NVIDIA_API_KEY" "$NVIDIA_API_BASE"
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
    # Ollama Cloud model ids carry a tag (":cloud" for the hosted big models),
    # per https://ollama.com/search. The bare names below resolve to a local
    # pull that the cloud endpoint does not have -> 404 "model not found".
    # NOTE: the :cloud models require an Ollama paid subscription; without one
    # the upstream returns 403. The verification pass at the end will surface
    # this clearly (a 403/404 here is an upstream entitlement issue, not a
    # config bug).
    add_model "ollama/deepseek-v4-flash" "openai/deepseek-v4-flash:cloud" "OLLAMA_API_KEY" "$OLLAMA_API_BASE"
    add_model "ollama/deepseek-v4-pro" "openai/deepseek-v4-pro:cloud" "OLLAMA_API_KEY" "$OLLAMA_API_BASE"
    add_model "ollama/glm-5.2" "openai/glm-5.2:cloud" "OLLAMA_API_KEY" "$OLLAMA_API_BASE"
    add_model "ollama/minimax-m3" "openai/minimax-m3:cloud" "OLLAMA_API_KEY" "$OLLAMA_API_BASE"
    add_model "ollama/qwen3.5" "openai/qwen3.5:cloud" "OLLAMA_API_KEY" "$OLLAMA_API_BASE"
    add_model "ollama/kimi-k2.7-code" "openai/kimi-k2.7-code:cloud" "OLLAMA_API_KEY" "$OLLAMA_API_BASE"
fi

echo "All models requested have been registered."
echo "You can check them at $LITELLM_URL/ui/?page=models"

# =============================================================================
# Verification pass: prove callability, not mere presence in /v1/models.
# Sends a real 1-token completion through LiteLLM for every registered alias and
# prints a PASS/FAIL health table. Controlled by REGISTER_MODELS_VERIFY (default
# on); set REGISTER_MODELS_VERIFY=0 to skip. A FAIL here is the real signal that
# a fallback link is unhealthy even though it shows up in /v1/models.
# =============================================================================
if [ "${REGISTER_MODELS_VERIFY:-1}" != "0" ] && [ "${#REGISTERED[@]}" -gt 0 ]; then
    echo "========================================="
    echo "Verifying callability (1-token live probe per alias)..."
    echo "========================================="
    pass_count=0
    fail_count=0
    fail_list=()
    for alias_name in "${REGISTERED[@]}"; do
        # `|| true` keeps the non-zero FAIL return from tripping `set -e`.
        result="$(probe_model "$alias_name" || true)"
        if [ "$result" = "PASS" ]; then
            printf '  [PASS] %s\n' "$alias_name"
            pass_count=$((pass_count + 1))
        else
            printf '  [FAIL] %-28s %s\n' "$alias_name" "${result#FAIL }"
            fail_count=$((fail_count + 1))
            fail_list+=("$alias_name")
        fi
    done
    echo "-----------------------------------------"
    echo "Callable: $pass_count   Unhealthy: $fail_count   (of ${#REGISTERED[@]} registered)"
    if [ "$fail_count" -gt 0 ]; then
        echo "Unhealthy aliases (registered but NOT callable): ${fail_list[*]}"
        echo "These appear in /v1/models but fail a real call — check upstream"
        echo "model id, api_base, and account entitlement (e.g. 403 = subscription)."
    fi
fi
