# Quick Start: New Phone & Chrome Features

## 🚀 What's New?

Two new features have been implemented and committed to your project:

### 1. Intelligent Phone Field Handling
Different phone numbers for different phone field types (mobile, home, work)

### 2. Force Chrome Browser
Agent now always uses real Chrome, never falls back to chromium

---

## 📱 Using Different Phone Numbers

### Setup
Edit `config.py` and set your phone numbers:

```python
@dataclass(frozen=True)
class UserProfile:
    phone: str = "(571) 354-5212"          # Primary phone (fallback)
    phone_mobile: str = "(571) 111-2222"   # Your mobile number
    phone_home: str = "(571) 333-4444"     # Your home number
    phone_work: str = "(571) 555-6666"     # Your work number
```

### How It Works
When a form has these fields:
- **"Mobile Phone" or "Cell Phone"** → Uses `phone_mobile`
- **"Home Phone"** → Uses `phone_home`
- **"Work Phone"** → Uses `phone_work`
- **Generic "Phone"** → Uses primary `phone`

### Example
```
Form has:
  Cell Phone: [    ]      ← Gets filled with phone_mobile
  Home Phone: [    ]      ← Gets filled with phone_home
  Work Phone: [    ]      ← Gets filled with phone_work
```

### If You Leave Them Blank
Leave `phone_mobile`, `phone_home`, `phone_work` empty (as `""`), and the agent will use your primary `phone` value for everything. This is backward compatible with your current setup.

---

## 🌐 Chrome Browser Mode

### What Changed
**Before:** If your Chrome was open, agent used bundled Chromium  
**Now:** Agent always uses your real Chrome (even if it's already open)

### Important ⚠️
Your Chrome browser will be **locked while applications run**. You cannot:
- Browse in your own Chrome while agent is filling applications
- Use Chrome for other tasks until the agent finishes

### Why This Is Better
Real Chrome matches exactly how forms render when you test them manually. Chromium sometimes renders differently, which could lead to:
- Fields appearing in different positions
- Different font rendering
- Different behavior for JavaScript-heavy forms

### No Action Needed
The default `browser_channel = "chrome"` is already set. It will work automatically.

---

## ✅ Verification Checklist

After pulling these changes:

### Check 1: Phone Fields
```bash
# Verify config.py has the new phone fields
grep "phone_mobile\|phone_home\|phone_work" config.py
# Should show 3 lines
```

### Check 2: Chrome Mode
```bash
# Verify _choose_channel was updated
grep "always use real Chrome\|never fallback" browser_automation.py
# Should show the new comment
```

### Check 3: Git Commit
```bash
# Verify the commit exists
git log --oneline | grep "intelligent phone"
# Should show: c1c055f Add intelligent phone field handling and force Chrome browser
```

---

## 🧪 Testing

### Test 1: Phone Field Detection
1. Find a job posting with **separate mobile/home/work phone fields**
2. Update `config.py` with different numbers:
   ```python
   phone_mobile: str = "(571) 111-2222"
   phone_home: str = "(571) 333-4444"
   phone_work: str = "(571) 555-6666"
   ```
3. Run: `python apply.py`
4. Verify each field gets the correct phone type

### Test 2: Chrome Verification
1. Have your Chrome browser open (don't close it)
2. Run: `python apply.py`
3. Notice: Agent uses your open Chrome (doesn't fallback to Chromium)
4. Watch task manager: Should see your Chrome working, not a separate browser

---

## 📋 Summary

| Feature | Before | After |
|---------|--------|-------|
| **Phone Fields** | Always used primary phone | Can use different numbers by type |
| **Browser** | Fell back to Chromium if Chrome open | Always uses real Chrome |
| **Config** | `phone` field | `phone`, `phone_mobile`, `phone_home`, `phone_work` |
| **Browsing** | Could browse while agent works | Chrome locked to agent (better rendering) |

---

## 💡 Pro Tips

1. **Set phone_mobile, phone_home, phone_work to real numbers** - Most job forms have at least one of these fields
2. **Keep primary phone set** - Used as fallback if specific type not configured
3. **Close Chrome if you need it** - Since Chrome is locked, open it after agent finishes
4. **Check logs** - Agent logs which phone number filled which field

---

## Questions?

If phone fields aren't being filled correctly:
1. Check the form's field label/placeholder/id (the agent's logs show what it detects)
2. Verify the label contains keywords like "mobile", "cell", "home", or "work"
3. If not, it might need a new keyword pattern added

If Chrome issues occur:
1. Ensure `browser_channel` is set to "chrome" in config or .env
2. Check that real Chrome is installed
3. Verify no other Chrome process is already using the profile

---

## Commit Info
```
Commit: c1c055f
Message: Add intelligent phone field handling and force Chrome browser
Files: config.py, browser_automation.py
```

Ready to apply jobs with better phone number handling and real Chrome rendering! 🎉
