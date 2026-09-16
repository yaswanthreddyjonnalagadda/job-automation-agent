# Diagnostic Checklist for Multi-Page Form Issues

## Step 1: Verify Deployment ✅

Run this to check which version you're using:

```bash
# Check if you're using the FIXED version
grep "Multipage_AutoAdvance" /path/to/your/browser_automation.py

# If you see NO output above, you're still using the OLD version
# Deploy the new one:
cp /mnt/user-data/outputs/browser_automation_fixed.py /path/to/your/browser_automation.py
```

---

## Step 2: Look for These Log Messages

### If Using OLD Version (Still Shows These):
```
NEXT_STUCK: clicking Next/Continue didn't change the page
```
**ACTION:** Copy the new browser_automation_fixed.py to your project

### If Using NEW Version (Should Show These):
```
Multipage_AutoAdvance: Clicking 'Next' button to proceed to next page
Multipage_AutoAdvance: Detected X fillable fields on new page
Multipage_AutoAdvance: Auto-filled X fields
```

---

## Step 3: If Still Not Working

Run the agent again and share:

1. **Full log output** — paste everything from the start to where it stops
2. **Screenshot** — show what's visible on the page when it stops
3. **HTML source** — what's the actual text of the Next button?

To get button text:
```
Right-click the Next button → Inspect → Look for the button element
What does it say? Is it "Next", "Continue", "Save and Continue", etc.?
```

---

## Step 4: Common Issues

### Issue 1: Button Not Found
**Log:** `No Next/Continue button found`  
**Fix:** Check button text and add it to _NEXT_SELECTORS if it's different

### Issue 2: Button Is Disabled
**Log:** `The form's Next is disabled`  
**Fix:** Required fields aren't filled — manual review needed

### Issue 3: Page Doesn't Change
**Log:** `No fillable fields detected (attempt 1/2/3)`  
**Fix:** Page loaded but has no form fields — might be an error page

### Issue 4: Still Shows NEXT_STUCK
**Fix:** You haven't deployed browser_automation_fixed.py yet

---

## Step 5: Deploy and Test

```bash
# 1. Copy the new file
cp /mnt/user-data/outputs/browser_automation_fixed.py ./browser_automation.py

# 2. Verify syntax
python3 -m py_compile browser_automation.py

# 3. Run agent
python3 your_agent.py

# 4. Check logs for Multipage_AutoAdvance messages
```

---

## Share This Information

After running, paste:
```
1. First 20 lines of logs (should show file being used)
2. Last 50 lines of logs (where it stops)
3. Screenshot of the form when it stops
4. The exact button text visible on that page
```

This will help diagnose exactly what's happening!
