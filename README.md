# PC Benchmark Manager

**[English](#english) | [العربية](#العربية)**

---

<a id="english"></a>

## English

PC Benchmark Manager is a desktop app (Python, PySide6, SQLite) that finds, installs, runs and records PC benchmarks:
processor, graphics card, games, storage, AI and web. Every result is stored with the PC's specs, so runs on
different computers can be compared, and each session ends with an HTML report.

### What it does

- **This PC**: processor, memory, graphics, operating system and platform on one screen (Copy specs).
- **Tests**: every test this PC can run, grouped as CPU, GPU, Games, Storage, AI and Web. Each row says whether
  it is installed, how it runs (automatic, assisted or manual), whether it was verified on a real run and roughly
  how long it takes.
- **One-click install**: a missing test gets an Install button. It downloads only from the official source
  (winget, the vendor's site or GitHub release with a pinned size and SHA-256, or your Linux distribution's
  package manager) and tells you what it will download first.
- **Run all tests**: installs what is missing (after one question), then runs everything in one session with
  warm-up runs, measured runs, a pause between runs, an emergency stop and resume after a crash.
- **Reports**: one HTML report per session, with its own section per test and a chart per run. Export and backup
  of the database.
- **Games**: frame capture with PresentMon (Windows) or MangoHud (Linux), forced graphics settings and built-in
  benchmark scripts (Cyberpunk 2077).
- **Telemetry**: temperatures, clocks and power during a run where the hardware reports them.
- **Advanced**: dashboard, library, history, comparison, Steam and run profiles.

### Tests

| Group | Tests |
|---|---|
| CPU | 7-Zip, Cinebench 2026 (multi and single core), y-cruncher (Pi 1B and 2.5B), Blender (CPU), V-Ray (CPU), PassMark CPU and memory, HandBrake (x264 and x265) |
| GPU | Blender (GPU), V-Ray (GPU and RTX), PassMark 2D and 3D |
| Games | Steam games with frame capture; Cyberpunk 2077 built-in benchmark |
| Storage | DiskSpd (Windows), fio |
| AI | MLPerf Client (Phi-4 mini, Llama 3.1 8B, Phi-4 reasoning, Qwen3 8B), llama-benchy with LM Studio (Qwen3.5 4B, Gemma 4 12B, Qwen3.8 27B, plus any model you add) |
| Web | Speedometer, JetStream |

Some tests are Windows only; the Tests screen hides what this PC cannot run and says why
(**Show tests this PC cannot run**).

### Requirements

- Windows 10/11 (x64 or ARM64) or Linux (Debian/Ubuntu or Arch; not every test is checked on Linux yet).
- Python 3.10 or newer.

### Install and run

Windows (PowerShell):

```powershell
git clone https://github.com/detailstech93/pc-benchmark-manager.git
cd pc-benchmark-manager
py -3.10 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe scripts\main.py
```

Or double-click `run.bat` after the setup.

Linux:

```bash
git clone https://github.com/detailstech93/pc-benchmark-manager.git
cd pc-benchmark-manager
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python scripts/main.py
```

Tests: `pip install -r requirements-dev.txt`, then `python -m pytest -q`.

### Safety

- Downloads only from official sources, always shown and confirmed first.
- Never changes system settings; a program that needs administrator rights asks through Windows (UAC) or pkexec.
- A failed run never becomes a score. Nothing is uploaded.
- Database changes are additive only: older databases keep working.

### More documentation

- [Full technical details](docs/DETAILS.md)
- [Linux test guide](docs/LINUX_TEST_GUIDE.md)
- [Professional benchmarks](docs/PRO_BENCHMARKS.md)
- [What happens when something fails](docs/FAILURE_CASES.md)
- [Packaged build](packaging/README.md)

---

<a id="العربية"></a>

<div dir="rtl">

## العربية

**PC Benchmark Manager** تطبيق سطح مكتب (Python و PySide6 و SQLite) بيلاقي اختبارات الأداء للكمبيوتر، بيثبّتها،
بيشغّلها وبيسجّل نتائجها: المعالج، كرت الشاشة، الألعاب، التخزين، الذكاء الاصطناعي والويب. كل نتيجة بتنحفظ مع
مواصفات الجهاز، لهيك فيك تقارن بين أجهزة مختلفة، وكل جلسة بتخلص بتقرير HTML.

### شو بيعمل

- **هذا الجهاز**: المعالج والذاكرة وكرت الشاشة ونظام التشغيل والمنصة بشاشة وحدة (مع زر نسخ المواصفات).
- **الاختبارات**: كل اختبار بيقدر هالجهاز يشغّله، مقسّمة: معالج، كرت شاشة، ألعاب، تخزين، ذكاء اصطناعي وويب. كل سطر
  بيقلك إذا الاختبار مثبّت، كيف بيشتغل (تلقائي، بمساعدة أو يدوي)، إذا انجرّب على تشغيل حقيقي، وقديش بياخد وقت تقريباً.
- **تثبيت بكبسة وحدة**: الاختبار الناقص إلو زر Install. بينزّل بس من المصدر الرسمي (winget، موقع الشركة أو GitHub
  مع حجم و SHA-256 مثبّتين، أو مدير الحزم بلينكس) وبيقلك شو رح ينزّل قبل ما يبلّش.
- **تشغيل كل الاختبارات**: بيثبّت الناقص (بعد سؤال واحد) وبعدين بيشغّل الكل بجلسة وحدة، مع تشغيلات تحمية، تشغيلات
  مقاسة، استراحة بين التشغيلات، زر إيقاف طوارئ، ومتابعة بعد أي انهيار.
- **التقارير**: تقرير HTML لكل جلسة، كل اختبار إلو قسمه الخاص ورسم بياني لكل تشغيل. مع تصدير ونسخ احتياطي لقاعدة
  البيانات.
- **الألعاب**: قياس الفريمات بـ PresentMon (ويندوز) أو MangoHud (لينكس)، فرض إعدادات الغرافيك، وسكربتات للبنشمارك
  المدمج بالألعاب (Cyberpunk 2077).
- **الحساسات**: الحرارة والترددات والاستهلاك أثناء التشغيل إذا القطع بتعطيها.
- **متقدم**: لوحة التحكم، المكتبة، السجل، المقارنة، Steam وملفات إعدادات التشغيل.

### الاختبارات

| القسم | الاختبارات |
|---|---|
| المعالج | 7-Zip، Cinebench 2026 (كل الأنوية ونواة وحدة)، y-cruncher (Pi 1B و 2.5B)، Blender (CPU)، V-Ray (CPU)، PassMark CPU والذاكرة، HandBrake (x264 و x265) |
| كرت الشاشة | Blender (GPU)، V-Ray (GPU و RTX)، PassMark 2D و 3D |
| الألعاب | ألعاب Steam مع قياس الفريمات؛ البنشمارك المدمج بـ Cyberpunk 2077 |
| التخزين | DiskSpd (ويندوز)، fio |
| الذكاء الاصطناعي | MLPerf Client (Phi-4 mini، Llama 3.1 8B، Phi-4 reasoning، Qwen3 8B)، و llama-benchy مع LM Studio (Qwen3.5 4B، Gemma 4 12B، Qwen3.8 27B، وأي موديل بتضيفه انت) |
| الويب | Speedometer، JetStream |

في اختبارات بتشتغل عالويندوز بس؛ شاشة الاختبارات بتخبّي يلي ما بيشتغل عهالجهاز وبتقلك ليش
(**Show tests this PC cannot run**).

### المتطلبات

- ويندوز 10/11 (x64 أو ARM64) أو لينكس (Debian/Ubuntu أو Arch؛ مش كل الاختبارات انجرّبت عاللينكس لسا).
- Python 3.10 أو أحدث.

### التثبيت والتشغيل

أوامر التثبيت نفسها يلي بالقسم الإنكليزي فوق ([Install and run](#install-and-run)): بتنسخ المشروع، بتعمل بيئة
`.venv`، بتثبّت `requirements.txt`، وبتشغّل `scripts/main.py`. عالويندوز فيك بعدها تكبس دبل كليك على `run.bat`.

### الأمان

- التنزيل بس من المصادر الرسمية، وبينعرض عليك وبتوافق قبل أي تنزيل.
- ما بيغيّر إعدادات النظام أبداً؛ البرنامج يلي بدو صلاحيات مدير بيطلبها عن طريق ويندوز (UAC) أو pkexec.
- التشغيل الفاشل ما بينحسب نتيجة أبداً. وما في شي بينرفع عالإنترنت.
- تغييرات قاعدة البيانات بس إضافات: قواعد البيانات القديمة بتضل تشتغل.

### توثيق أكتر (بالإنكليزي)

- [كل التفاصيل التقنية](docs/DETAILS.md)
- [دليل التجربة عاللينكس](docs/LINUX_TEST_GUIDE.md)
- [البنشماركات الاحترافية](docs/PRO_BENCHMARKS.md)
- [شو بيصير لما شي يفشل](docs/FAILURE_CASES.md)
- [النسخة المجمّعة](packaging/README.md)

</div>
