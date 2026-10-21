**Hawkeye Jobs** هو تطبيق مجاني ومفتوح المصدر يبحث لك عن الوظائف في LinkedIn وIndeed وBayt وNaukriGulf وGoogle Jobs، ويعمل كاملًا على جهازك.
Hawkeye Jobs is a free, open-source job finder for LinkedIn, Indeed, Bayt, NaukriGulf and Google Jobs. It runs entirely on your computer.

## التنزيل · Download

| النظام · System | الملف · File |
|---|---|
| macOS (Apple M1/M2/M3/M4) | `HawkeyeJobs-macOS-AppleSilicon.dmg` |
| macOS (Intel) | `HawkeyeJobs-macOS-Intel.dmg` |
| Windows 10 / 11 | **Microsoft Store:** https://apps.microsoft.com/detail/9PB9XTPR0XH4 (موصى به · recommended) — `HawkeyeJobs-Windows-Setup.exe` (برنامج تثبيت · installer) — أو · or `HawkeyeJobs-Windows.zip` |
| Linux (x86-64) | `HawkeyeJobs-Linux.AppImage` — أو · or `HawkeyeJobs-Linux.tar.gz` |

يتطلب البحث في Bayt وNaukriGulf وGoogle Jobs وجود متصفح Google Chrome.
Bayt, NaukriGulf and Google Jobs need Google Chrome installed.

## التشغيل أول مرة · First launch

**macOS**
افتح ملف ‎.dmg واسحب Hawkeye Jobs إلى مجلد Applications، ثم افتحه. التطبيق موقَّع وموثَّق من Apple، فيفتح دون أي تحذير.
Open the .dmg, drag Hawkeye Jobs into Applications and open it. The app is signed and notarized by Apple, so it opens with no warning.

**Windows — الأسهل · easiest:** ثبّته من Microsoft Store: موقَّع من Microsoft، ويعمل دون تحذير، ويُحدَّث تلقائيًا.
Install it from the Microsoft Store: signed by Microsoft, no warning, updated automatically.
https://apps.microsoft.com/detail/9PB9XTPR0XH4

ملفات Windows هنا غير موقَّعة بشهادة مدفوعة، لذلك يعرض Windows تحذيرًا في المرة الأولى فقط.
The Windows files here are not signed with a paid certificate, so Windows warns you the first time only.

**Windows — أسهل طريقة · easiest way**

افتح PowerShell من قائمة Start والصق هذا السطر (يُنزِّل التطبيق ويثبّته ويفتحه دون تحذير):
Open PowerShell from the Start menu and paste this line (downloads, installs and opens the app with no warning):

```
irm https://abdallahabusnineh.github.io/hawkeye-jobs/install.ps1 | iex
```

**Windows — من الملف · from the file**
1. شغّل `HawkeyeJobs-Windows-Setup.exe` (لا يحتاج صلاحيات المسؤول).
   Run `HawkeyeJobs-Windows-Setup.exe` (no administrator rights needed).
2. إذا ظهرت رسالة «حمى Windows جهاز الكمبيوتر»، فاضغط «مزيد من المعلومات» ثم «التشغيل على أي حال».
   If Windows shows "Windows protected your PC", click **More info** → **Run anyway**.

**Linux**
اجعل ملف ‎.AppImage‎ قابلًا للتشغيل ثم انقر عليه مرتين.
Make the .AppImage executable, then double-click it: `chmod +x HawkeyeJobs-Linux.AppImage`

## الخصوصية · Privacy

لا يوجد خادم ولا حساب. لا يخرج من جهازك إلا طلبات البحث نفسها، وفحص التحديثات اليومي (يمكن إيقافه من الإعدادات)، والصفحات التي تفتحها أنت.
No server, no account. Nothing leaves your computer except the searches themselves, the daily update check (can be turned off in Settings), and pages you open.

يمكنك التحقق من سلامة الملفات بمقارنتها مع `SHA256SUMS.txt`.
You can verify the downloads against `SHA256SUMS.txt`.
