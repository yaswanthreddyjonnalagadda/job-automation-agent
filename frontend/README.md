# Job Agent — Modern Autonomous Application Dashboard

A sleek, developer-focused "deep dark mode" dashboard for the Job Automation Agent, inspired by modern CI/CD interfaces (Vercel, GitHub Actions).

## Tech Stack
- **Framework:** React 18 + TypeScript
- **Styling:** Tailwind CSS (custom deep dark palette)
- **Icons:** Lucide React
- **Build Tool:** Vite

## Design Tokens & Palette
- **Base Background:** `#0B0F19` (Deep slate / navy)
- **Card Surface:** `#131B2E` with border `#1E293B`
- **Primary Accent / CTAs:** Indigo-600 (`#4F46E5`) with glow `shadow-indigo-500/20`
- **Text Primary:** `#F1F5F9` (slate-100)
- **Text Secondary:** `#94A3B8` (slate-400)
- **Status Indicators:**
  - **Submitted:** `bg-emerald-500/10 text-emerald-400 border-emerald-500/20`
  - **Skipped:** `bg-slate-500/10 text-slate-400 border-slate-500/20`
  - **Needs Attention:** `bg-amber-500/10 text-amber-400 border-amber-500/20 animate-pulse`
  - **In Progress:** `bg-indigo-500/10 text-indigo-400 border-indigo-500/20`

## Component Architecture
1. **`Navbar.tsx`**: Top navigation with brand identity ("Job Agent"), active indicator pill on "Applications", navigation tabs, and agent runtime heartbeat indicator.
2. **`QuickIntake.tsx`**: Segmented progress bar (Submitted, Need attention, In progress, Skipped) with stats counter (`82 tracked · 71 submitted · 11 skipped`), quick URL intake input with ATS verification, and safety policy disclaimers.
3. **`LiveMonitor.tsx`**: Active agent execution monitor with radar activity indicator, elapsed runtime, current step status, interactive desktop indicator, and `[View log]` / `[Reload agent code]` actions.
4. **`SuggestedJobs.tsx`**: Compact grid feed with match percentage pills, company/role links, metadata tags (*Remote*, *Greenhouse*, *Senior-Level*), category filters (*Best matches*, *Remote*, *Greenhouse*, *Lever*), and `[Start application]` / `[Not interested]` actions.
5. **`ApplicationTable.tsx`**: High-performance data table with tab filters (*All*, *Needs you*, *In progress*, *Submitted*, *Skipped*), real-time company/role search, status badges, timestamp, and row action menus (*Details*, *Delete*, *Mark as submitted*).
6. **`LogModal.tsx`**: Live Playwright terminal execution log viewer with line numbers and one-click clipboard copy.
7. **`DetailModal.tsx`**: Modal for inspecting application metadata, tailored resume filenames, and ATS telemetry notes.

## Development & Build
```bash
# Run local dev server (port 3000)
npm run dev

# Production build
npm run build

# Preview build
npm run preview
```

