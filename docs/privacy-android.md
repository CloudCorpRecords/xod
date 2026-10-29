# Xod for Android: privacy policy

Xod is a free, open app. There is no Xod account and no Xod server: the app talks to your own computer, or to an AI
service you choose, and nothing else. We don't collect, sell or share your data, and there are no ads or analytics.

## What the app does with your data

**Paired with your computer.** The app pairs with Xod running on your own computer, by QR code or Bluetooth. After that,
everything between them goes over Agent Mail, end-to-end encrypted and signed (X25519, ChaCha20-Poly1305, Ed25519).
It travels directly between your devices, on your own network or over your Tailscale network, which you set up. Your
chats, tasks and files are kept on your computer, not on the phone or anywhere else.

**Xod on this phone (optional).** If you add your own API key for an AI service (OpenAI, Anthropic, Google Gemini,
OpenRouter, Groq or Fireworks), your messages in that chat go straight from the phone to that service, under its own
privacy policy. The key is encrypted with a key held in the Android Keystore. Those chats are kept only in the app's
private storage on the phone.

**Abilities you switch on.** Each one is off until you turn it on in the app, and you can turn it off at any time:

- **Screen and Tap (Accessibility service).** Lets your computer's Xod read the screen or tap it when you ask it to,
  and lets "Approve for me" press Allow in the apps you pick. Screen content goes only to your own paired computer, end-to-end
  encrypted, and is never stored by the app. A notification shows while the service is on.
- **Notifications (notification access).** Lets your computer's Xod list your recent notifications, and lets "Approve for me"
  press an Allow button in notifications from the apps you pick. The last 50 notifications are kept in memory only.
- **Location, Contacts, Calendar, Files, Camera.** Read only when your computer's Xod asks for them for something you
  asked it to do, and sent only to your paired computer. The camera is used only to scan the pairing QR code.
- **Text messages** (the version from GitHub only, not Google Play). Xod asks you on the phone before every message.

Anything that sends, pays, deletes or unlocks asks you on the phone first.

## What leaves the phone

- To your paired computer: what you ask it for, end-to-end encrypted.
- To the AI service you chose, if you use Xod on this phone: your messages in that chat.
- Nothing else, to no one else.

## Deleting your data

Unpair in the app, or uninstall it, to delete everything the app has stored on the phone. Your computer's data stays on
your computer, where you can delete it in Xod.

## Children

Xod isn't directed at children under 13.

## Contact

Questions or problems: open an issue at https://github.com/CloudCorpRecords/xod/issues

Last updated: October 29, 2026.
