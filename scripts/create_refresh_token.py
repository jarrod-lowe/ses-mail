#!/usr/bin/env python3
from google_auth_oauthlib.flow import InstalledAppFlow

# Choose the narrowest scope you need:
#SCOPES = ["https://www.googleapis.com/auth/gmail.import"]
SCOPES = ["https://www.googleapis.com/auth/gmail.insert"]
# Or: ["https://mail.google.com/"]

def main():
    flow = InstalledAppFlow.from_client_secrets_file("client_secret.json", SCOPES)
    creds = flow.run_local_server(port=0)  # opens your browser for consent
    with open("token.json", "w") as f:
        f.write(creds.to_json())
    print("Wrote token.json (includes refresh token). Keep it safe.")

if __name__ == "__main__":
    main()
