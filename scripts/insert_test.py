#!/usr/bin/env python3
import base64
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

SCOPES = ["https://www.googleapis.com/auth/gmail.insert"]

creds = Credentials.from_authorized_user_file("token.json", SCOPES)
svc = build("gmail", "v1", credentials=creds, cache_discovery=False)

msg = EmailMessage()
msg["From"] = "updates@example.net"
msg["To"] = "me@example.net"
msg["Subject"] = "Fresh delivery-style test"
msg["Date"] = formatdate(localtime=True)
msg["Message-ID"] = make_msgid(domain="example.net")  # no In-Reply-To/References
msg.set_content("This is a delivery-style test inserted via Gmail API.")

raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("utf-8")
resp = svc.users().messages().insert(
    userId="me",
    body={"raw": raw, "labelIds": ["INBOX", "UNREAD"]},
    internalDateSource="receivedTime",  # stamp 'now'
).execute()
print("OK:", resp.get("id"))
