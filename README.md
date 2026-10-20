# PC Benchmark Manager

**[English](#english) | [العربية](#العربية)**

---

<a id="english"></a>

## English

PC Benchmark Manager is a desktop app (Python, PySide6, SQLite) that finds, installs, runs and records PC benchmarks:
processor, graphics card, games, storage, AI and web. Every result is stored with the PC's specs, so runs on
different computers can be compared, and each session ends with an HTML report.

> **Beta.** Tests marked "not verified yet" on the Tests screen have not been checked on a real run, and Linux has
> not been tried on a real PC yet. Please report what fails (Issues).

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
- **Remote mode**: an on/off switch on the Tests screen. When it is on, a phone or another computer on the same
  local network opens the address the app shows, types its one-time code, sees every test, starts them, follows
  each test and run with its scores and the time left, stops them and reads the report right there. Off by
  default; no internet.
- **Reports folder**: asked once, the first time; every report is saved there.
- **Telemetry**: temperatures, clocks and power during a run where the hardware reports them.
- **Advanced**: dashboard, library, history, comparison, Steam and run profiles.

### Tests

| Group | Tests |
|---|---|
| CPU | 7-Zip, Cinebench 2026 (multi and single core), Geekbench 6 and 7, y-cruncher (Pi 1B and 2.5B), Blender (CPU), V-Ray (CPU), PassMark CPU and memory, HandBrake (x264 and x265), PugetBench (Photoshop, Premiere Pro) |
| GPU | Blender (GPU), V-Ray (GPU and RTX), PassMark 2D and 3D, Geekbench 6 and 7 (OpenCL, Vulkan; 7 also CUDA) |
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

Linux, one command (Debian/Ubuntu, Fedora, Arch, openSUSE; also WSL2 on Windows 11):

```bash
curl -fsSL https://raw.githubusercontent.com/detailstech93/pc-benchmark-manager/master/install.sh | bash
```

It installs the system packages the app needs (asks for your password once), downloads the app into
`~/pc-benchmark-manager`, installs its Python libraries in its own `.venv`, adds it to the applications menu and
starts it. Run the same command again to update. From a copy of the project: `bash install.sh`.

Tests: `pip install -r requirements-dev.txt`, then `python -m pytest -q`.

### Safety

- Downloads only from official sources, always shown and confirmed first.
- Never changes system settings; a program that needs administrator rights asks through Windows (UAC) or pkexec.
- A failed run never becomes a score. Nothing is uploaded, except by Geekbench: its free version always uploads
  each result to the Geekbench Browser, and the app says so before it runs.
- Database changes are additive only: older databases keep working.

### More documentation

- [Full technical details](docs/DETAILS.md)
- [Linux test guide](docs/LINUX_TEST_GUIDE.md)
- [Professional benchmarks](docs/PRO_BENCHMARKS.md)
- [What happens when something fails](docs/FAILURE_CASES.md)
- [Packaged build](packaging/README.md)

### License

GNU General Public License v3.0 ([LICENSE](LICENSE)): use, change and share it freely; a changed version you share
must stay under the same license, with its source. The benchmark programs the app installs keep their own licenses.

---

<a id="العربية"></a>

<div dir="rtl">

## العربية

**PC Benchmark Manager** تطبيق سطح مكتب (Python و PySide6 و SQLite) بيلاقي اختبارات الأداء للكمبيوتر، بيثبّتها،
بيشغّلها وبيسجّل نتائجها: المعالج، كرت الشاشة، الألعاب، التخزين، الذكاء الاصطناعي والويب. كل نتيجة بتنحفظ مع
مواصفات الجهاز، لهيك فيك تقارن بين أجهزة مختلفة، وكل جلسة بتخلص بتقرير HTML.

> **نسخة تجريبية (Beta).** الاختبارات المكتوب جنبها "not verified yet" ما انجرّبت لسا على تشغيل حقيقي، ولينكس ما
> انجرّب لسا على جهاز حقيقي. إذا شي فشل معك، خبّرنا (Issues).

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
- **وضع التحكم عن بعد**: زر تشغيل/إطفاء بشاشة الاختبارات. لما يكون شغّال، الموبايل أو أي كمبيوتر تاني عنفس الشبكة
  المحلية بيفتح العنوان يلي بيعرضه التطبيق، بيكتب الكود المؤقت، وبيشوف كل الاختبارات، بيشغّلها، بيتابع كل اختبار وتشغيل مع النتائج والوقت الباقي، بيوقفها وبيقرأ التقرير عنده.
- **مجلد التقارير**: التطبيق بيسألك مرة وحدة أول مرة وين بدك تنحفظ التقارير، وكل التقارير بتنحفظ فيه.
  مطفي بشكل افتراضي، وما بيستعمل إنترنت.
- **الحساسات**: الحرارة والترددات والاستهلاك أثناء التشغيل إذا القطع بتعطيها.
- **متقدم**: لوحة التحكم، المكتبة، السجل، المقارنة، Steam وملفات إعدادات التشغيل.

### الاختبارات

| القسم | الاختبارات |
|---|---|
| المعالج | 7-Zip، Cinebench 2026 (كل الأنوية ونواة وحدة)، Geekbench 6 و 7، y-cruncher (Pi 1B و 2.5B)، Blender (CPU)، V-Ray (CPU)، PassMark CPU والذاكرة، HandBrake (x264 و x265)، PugetBench (Photoshop و Premiere Pro) |
| كرت الشاشة | Blender (GPU)، V-Ray (GPU و RTX)، PassMark 2D و 3D، Geekbench 6 و 7 (OpenCL و Vulkan، و CUDA بـ 7) |
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

عاللينكس في أمر واحد بيعمل كل شي (Debian/Ubuntu و Fedora و Arch و openSUSE، وكمان WSL2 عويندوز 11): بيثبّت
مكتبات النظام (بيطلب الباسورد مرة وحدة)، بينزّل التطبيق عـ `~/pc-benchmark-manager`، بيثبّت مكتباته بـ `.venv` تبعه،
بيضيفه لقائمة البرامج وبيفتحه. نفس الأمر بيحدّثه بعدين:

<div dir="ltr">

```bash
curl -fsSL https://raw.githubusercontent.com/detailstech93/pc-benchmark-manager/master/install.sh | bash
```

</div>

### الأمان

- التنزيل بس من المصادر الرسمية، وبينعرض عليك وبتوافق قبل أي تنزيل.
- ما بيغيّر إعدادات النظام أبداً؛ البرنامج يلي بدو صلاحيات مدير بيطلبها عن طريق ويندوز (UAC) أو pkexec.
- التشغيل الفاشل ما بينحسب نتيجة أبداً. وما في شي بينرفع عالإنترنت، إلا Geekbench: نسخته المجانية دايماً بترفع
  كل نتيجة عـ Geekbench Browser، والتطبيق بيقلك هالشي قبل ما يشغّله.
- تغييرات قاعدة البيانات بس إضافات: قواعد البيانات القديمة بتضل تشتغل.

### توثيق أكتر (بالإنكليزي)

- [كل التفاصيل التقنية](docs/DETAILS.md)
- [دليل التجربة عاللينكس](docs/LINUX_TEST_GUIDE.md)
- [البنشماركات الاحترافية](docs/PRO_BENCHMARKS.md)
- [شو بيصير لما شي يفشل](docs/FAILURE_CASES.md)
- [النسخة المجمّعة](packaging/README.md)

### الرخصة

GNU General Public License v3.0 ([LICENSE](LICENSE)): فيك تستعمله وتعدّل عليه وتشاركه بحرية؛ وأي نسخة معدّلة
بتشاركها لازم تضل بنفس الرخصة ومع الكود تبعها. برامج الاختبار يلي بيثبّتها التطبيق إلها رخصها الخاصة.

</div>
