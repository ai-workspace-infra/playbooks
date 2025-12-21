import requests

def get_hcp_api_token(client_id, client_secret):
    """Obtain the HCP API token using client credentials."""
    url = "https://auth.idp.hashicorp.com/oauth2/token"
    headers = {
        "Content-Type": "application/x-www-form-urlencoded"
    }
    data = {
        "client_id": client_id,
        "client_secret": client_secret,
        "grant_type": "client_credentials",
        "audience": "https://api.hashicorp.cloud"
    }

    response = requests.post(url, headers=headers, data=data)
    response.raise_for_status()  # Raise an error for bad responses
    return response.json().get("access_token")

def get_secret_data(api_url, api_token):
    """
    Fetch the secret data from HCP Cloud using the API URL and token.

    Parameters:
    - api_url: The URL to fetch secret data from HCP Cloud.
    - api_token: The API token for authentication.

    Returns:
    - The JSON response containing the secret data.
    """
    headers = {
        "Authorization": f"Bearer {api_token}"
    }

    response = requests.get(api_url, headers=headers)
    response.raise_for_status()  # Raise an error for bad responses
    return response.json()

def get_secret_value_by_name(secret_data, secret_name):
    """
    Get the version value by the specified name from the fetched secret data.

    Parameters:
    - secret_data: The JSON data containing secrets fetched from HCP Cloud.
    - secret_name: The name of the secret to fetch the version value for.

    Returns:
    - The value of the secret for the specified name.
    """
    secrets = secret_data.get('secrets', [])
    for secret_info in secrets:
        if secret_info.get('name') == secret_name:
            return secret_info.get('version', {}).get('value')

    return None
