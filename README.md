# 📊 ClaudeUsage Tracker

A lightweight, open-source desktop widget to monitor your Claude.ai message limits in real-time.

![Showcase Image](showcase.png)



## ✨ Features

* **Usage Tracking**: Monitor both 5-hour and weekly usage limits at a glance.
* **Clickthrough Mode**: Widget stays visible but becomes transparent to mouse clicks.
* **Cloudflare Bypass**: Integrated automated login flow using `undetected-chromedriver` to securely handle authentication.
* **Customizable UI**: Adjust opacity, modify update intervals, and position the widget anywhere on your screen.
* **Persistent Session**: Log in once; the script securely stores your session token locally for a seamless "set and forget" experience.

---

## 🚀 Getting Started

1. Navigate to the /dist dir

2. Click the .exe  (feel free to recompile for security)

3. It will take a while, and open a browser (cloudflare bypass - +- 20 seconds)

4. Sign in to Claude like usual (check you're on the official website)

---

## 🛠 Controls

| Icon | Action | Description |
| :--- | :--- | :--- |
| **👆** | **Toggle Clickthrough** | Makes the widget transparent to clicks. Only this icon remains interactive. |
| **⟳** | **Manual Refresh** | Forces an immediate update of your usage data. |
| **⚙** | **Settings** | Adjust window opacity and the background polling interval. |
| **×** | **Close** | Safely exits the application and stops all background threads. |

> **Note**: Dragging the widget is disabled while **Clickthrough Mode** is active to prevent accidental movement.


**If you find this tool useful, please consider giving it a ⭐ on GitHub!**
