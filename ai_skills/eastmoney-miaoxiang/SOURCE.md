# Source

The six subdirectories in this package were downloaded from the official
Eastmoney Miaoxiang distribution links supplied for ClawHub installation on
2026-07-26. The downloaded archives did not include a license file; redistribution
authorization must be confirmed before a public release.

| Skill | Upstream version | Download | SHA-256 |
| --- | --- | --- | --- |
| mx-data | 1.0.5 | https://marketing.dfcfw.com/res/download/A620260331IHX67H.zip | `8C4EFF9DF2EEABA3BC29FA289346B4A5F660FD0B38E4F7FCC3A0FC7B304547F7` |
| mx-search | 1.0.5 | https://marketing.dfcfw.com/res/download/A620260331K5WDTK.zip | `C0A4B43D46B18619BF85DF9D4BB62BF6FF02BB64B3E75D2ED7F62A97BD6E0597` |
| mx-xuangu | 1.0.4 | https://marketing.dfcfw.com/res/download/A620260623PHDKPP.zip | `9C5B25EA3959509474431B168059B74B356F44C0F642882D288D27EB71FEB4D2` |
| mx-zixuan | 1.0.0 | https://marketing.dfcfw.com/res/download/A6202603314TMGR1.zip | `92D8889C70A112FE8C24AEAE1D6677130EB44F6E0A0371124003921FC8A1C2FA` |
| mx-moni | 1.0.4 | https://marketing.dfcfw.com/res/download/A620260529ON5BMY.zip | `80CBA4F9A56486826E195C8DE1F249D490C59D5DBD67FDA7D23BEEAEE99BBC23` |
| mx-poster | 1.0.0 | https://marketing.dfcfw.com/res/download/A6202606169PIWQO.zip | `BCC15F267D6FB943D9F1FD33A2AF3ACFC98BBC3002F187C5A26DFFB2F04A6887` |

## Cowork adaptations

- Added the root Cowork metadata and unified capability-center configuration.
- Replaced fixed OpenClaw output paths with the active Cowork workspace.
- Removed implicit `.env` credential discovery.
- Replaced the `mx-moni` curl subprocess with Python HTTP requests.
- Kept community interaction opt-in instead of automatically following a post.
- Added explicit UTF-8, error propagation, and credential-safe diagnostics.
