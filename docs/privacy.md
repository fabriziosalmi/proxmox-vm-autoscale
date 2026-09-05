---
title: Privacy
description: What data this documentation site collects, what the Proxmox VM Autoscale software itself transmits, and where your data goes.
---

# Privacy

Two separate questions: what this website does, and what the software does. Both answers are short.

## This website

This site is static HTML hosted on **GitHub Pages**. It is built from the repository by GitHub Actions and served by GitHub.

### What the site itself does

- **No analytics.** No Google Analytics, no Plausible, no Fathom, no pixel, no tag manager.
- **No cookies.** The site sets none.
- **No third-party embeds.** No YouTube, no Disqus, no chat widget, no social buttons.
- **No fonts loaded from a third party.** Typography uses the fonts already on your device.
- **No forms.** Nothing on this site accepts input, so there is nothing to submit.
- **Search runs in your browser.** The search index is a static file downloaded with the page; your queries are not sent anywhere.
- **Theme preference** (light/dark) is stored in your browser's `localStorage`. It stays on your device and is never transmitted.

### What GitHub does

GitHub Pages serves the site, and GitHub logs requests as part of operating that service. GitHub's own documentation states it collects visitors' IP addresses for security and to keep the service running, retaining them for up to 14 days.

That processing is GitHub's, not ours, and we neither receive nor have access to those logs. See the [GitHub Privacy Statement](https://docs.github.com/en/site-policy/privacy-policies/github-privacy-statement) and [GitHub Pages data collection](https://docs.github.com/en/pages/getting-started-with-github-pages/what-is-github-pages#data-collection).

### External links

Pages link to GitHub, the Proxmox documentation, and a handful of other sites. Following one takes you to a third party with its own policy.

### The Open Graph image

Social previews reference an image hosted by GitHub (`repository-images.githubusercontent.com`). It loads only when a platform generates a preview card, not when you read the site.

## The software

Proxmox VM Autoscale runs on your infrastructure. It has no telemetry, no update check, no crash reporting, and no call home of any kind.

### What it connects to

Only what you configure:

| Destination | When | Contents |
|---|---|---|
| Your Proxmox nodes, over SSH | Every cycle | `qm` and `pvesh` commands |
| Your Gotify server, over HTTPS | On scaling actions and errors, if enabled | Notification text: VMID, host name, usage percentage |
| Your SMTP relay | On scaling actions and errors, if enabled | The same text, as email |
| Your `webhook_url` | On billing report generation, if configured | The billing report as JSON |

Every one of those is a host **you** name in `config.yaml`. The project operates none of them and receives nothing.

### What it stores locally

| Data | Location | Contents |
|---|---|---|
| Log | `/var/log/vm_autoscale.log` | Host names, VMIDs, usage figures, actions taken |
| Billing state | `billing_data.json` in `csv_output_dir` | Timestamped CPU and RAM specs per VM |
| Billing reports | CSV in `csv_output_dir` | Costed period summaries |
| Configuration | `config.yaml` | **Your credentials, in plain text** |

All of it stays on your machine. Nothing is uploaded, and there is no retention policy beyond what you set — the log does not rotate on its own and the billing file is never pruned. See [operations](/guide/operations#log-rotation).

### If you run it for customers

The billing data records how much CPU and RAM each VM held over time, which for a hosting provider is customer information. Your obligations under GDPR or equivalent law are yours, not this project's: it is software you run, not a service anyone operates for you.

## Contact

Questions about this page, or about the project's handling of data: **fabrizio.salmi@gmail.com**.

Security issues: [responsible disclosure](/security/disclosure), not email to the address above without reading that page first.

---

*Last reviewed: September 2026. Changes to this page are tracked in the [repository history](https://github.com/fabriziosalmi/proxmox-vm-autoscale/commits/main/docs/privacy.md).*
