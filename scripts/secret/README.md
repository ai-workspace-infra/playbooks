# Secret Management Script

This script is designed to fetch and manage secrets from HCP Cloud Secrets. It retrieves secrets based on environment variables and writes the final configuration to a JSON file.

# Prerequisites

1. **Python 3**: Ensure Python 3 is installed on your system.
2. **Python Libraries**: This script requires the `requests`, `pyyaml`, and `secret` libraries. You can install these dependencies using pip:

```bash
pip install requests pyyaml
```

# Environment Variables

The script requires the following environment variables:

- HCP_API_URL: The API URL for fetching secrets from HCP.
- HCP_CLIENT_ID: The client ID for HCP authentication.
- HCP_CLIENT_SECRET: The client secret for HCP authentication.

# Usage

To use this script, follow these steps:
Set Environment Variables: Ensure all required environment variables are set. For example:

```
export HCP_API_URL="https://api.cloud.hashicorp.com/secrets/..."
export HCP_CLIENT_ID="your_client_id"
export HCP_CLIENT_SECRET="your_client_secret"
```

# Functions

## get_hcp_api_token(client_id, client_secret)
Obtains an HCP API token using the provided client ID and secret.

## get_secret_data(api_url, api_token)
Fetches secret data from HCP Cloud using the provided API URL and token.

## get_secret_value_by_name(secret_data, secret_name)
Extracts the value of a secret from the fetched secret data based on the provided name.

# License
This script is licensed under the GPLv3 License. See the LICENSE file for more details.
