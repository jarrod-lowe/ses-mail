#!/usr/bin/env python3
import base64
from email.message import EmailMessage
from email.utils import formatdate, make_msgid

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

SCOPES = ["https://www.googleapis.com/auth/gmail.insert"]

def main():
    creds = Credentials.from_authorized_user_file("token.json", SCOPES)
    service = build("gmail", "v1", credentials=creds, cache_discovery=False)

    msg = EmailMessage()
    msg["From"] = "test3@example.com"
    msg["To"] = "test3@example.com"
    msg["Subject"] = "Gmail API import test (UNREAD) #3"
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain="local.test")
    msg.set_content("Imported via Gmail API and kept UNREAD.\n")

    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("utf-8")

    # NOTE: include UNREAD to keep it unread
    body = {"raw": raw, "labelIds": ["INBOX", "UNREAD"]}
    resp = service.users().messages().import_(userId="me", body=body).execute()
    print("OK:", resp.get("id"))

if __name__ == "__main__":
    main()
