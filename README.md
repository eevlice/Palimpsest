# Palimpsest

A local, self-contained book-translation tool. It runs on your own computer.
Your manuscript and your API key never leave your machine except for the
translation calls themselves. It reads a book once to build a brief and term
sheet you control, then translates paragraph by paragraph — drafting,
reviewing, and refining each in context — and saves your work automatically.

---

## First-time setup (about five minutes, once)

1. **Install Python 3.10 or newer** from https://python.org if you don't have it.
   (On Windows, tick "Add Python to PATH" during install.)

2. **Add your API key.** Open `key.txt.example`, paste your Anthropic key on the
   first line, and rename the file to **`key.txt`**. Get a key at
   https://console.anthropic.com → API keys. The key stays on your computer.

3. That's it. The launcher installs the three small libraries for you the first
   time it runs.

---

## To use it (every time)

- **Mac:** double-click **`start.command`**
- **Windows:** double-click **`start.bat`**

Your browser opens to the app automatically. When you're done, close the
browser tab and close the little terminal window the launcher opened.

> If double-clicking `start.command` on Mac is blocked the first time, right-click
> it → Open → Open. You only need to do that once.

---

## How it works

1. **Start a book.** Give it a name, set the languages, drop in a `.txt`, `.md`,
   or `.docx` file, and click *Read the book & build context*.
2. **Review the context.** Edit the auto-generated brief and term sheet. This
   travels with every paragraph. Optionally paste a style sample to match.
3. **Translate.** Source paragraphs sit on the left, your translations on the
   right. Click *Translate* on a paragraph to run the draft→review→refine loop.
   Edit the result, then *Approve*. The margin note shows what the review caught.
4. **It saves itself.** Every change is written to disk. Close it whenever; your
   books are waiting on the *Your books* screen when you come back — even months
   later.
5. **Export** writes your finished translation to a text file.

---

## Where your work lives

Each book is a JSON file in the **`projects/`** folder next to this README.
To back up or move your work, copy that folder. To hand a project to someone
else, send them the one JSON file.

---

## If something doesn't work

- **"No API key found"** — make sure the file is named exactly `key.txt` (not
  `key.txt.txt`) and sits in this folder, then refresh the page.
- **The browser didn't open** — go to http://localhost:5000 manually.
- **"python not found"** — Python isn't installed or wasn't added to PATH;
  reinstall from python.org and try again.

---

## What it costs

You pay Anthropic directly for the API calls (nothing flows through anyone
else). The one-time whole-book read and each paragraph's cost are shown to you
as you go. A full-length novel typically runs a few dollars to low double
digits depending on the model you pick.
