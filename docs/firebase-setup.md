# Firebase push setup (budget alerts)

Budget alerts reach the phone as Android notifications through Firebase Cloud Messaging (FCM). The server
only makes outbound HTTPS calls to Google; the phone receives pushes even when the app is closed or
Tailscale is off. Free tier.

## 1. Add Firebase to the existing project

At <https://console.firebase.google.com> choose **Add project**, then pick the existing Google Cloud
project you created for Gmail ingestion, or a new one if you skipped that. Google Analytics isn't needed.

Make sure the **Firebase Cloud Messaging API** is enabled:
<https://console.cloud.google.com/apis/library/fcm.googleapis.com?project=YOUR_PROJECT_ID>

## 2. A server credential that can only send pushes

Firebase's own "Generate new private key" gives an admin key for the whole project. A dedicated
service account with one role is safer:

1. <https://console.cloud.google.com/iam-admin/serviceaccounts?project=YOUR_PROJECT_ID> > **Create service account**
2. Name `xpensimate-push`, role **Firebase Cloud Messaging API Admin**, then **Done**.
3. Open the account > **Keys > Add key > Create new key > JSON**. A `.json` file downloads.

## 3. Put it on the server

```bash
scp ~/Downloads/xpensimate-*.json you@your-server:~/xpensimate/backend/secrets/fcm-service-account.json
```

Then on the server:

```bash
chmod 600 ~/xpensimate/backend/secrets/fcm-service-account.json
cd ~/xpensimate/backend && docker compose --profile gmail up -d --force-recreate api gmail-worker
```

Without this file the server still records alerts (`GET /api/v1/alerts`); it just doesn't push them.

## 4. Android app (M7)

In the Firebase console, **Add app > Android** with the app's package name and download
`google-services.json` into `android/app/`. The app registers its token with `POST /api/v1/devices`;
`POST /api/v1/devices/test` sends a test notification to every registered phone.
