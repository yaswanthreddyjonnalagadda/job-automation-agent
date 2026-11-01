import React, { useState } from 'react';
import { Link2, ArrowRight, ShieldAlert } from 'lucide-react';
import { ApplicationStats } from '../types/dashboard';

interface QuickIntakeProps {
  stats: ApplicationStats;
  onSubmitUrl: (url: string) => void;
}

export const QuickIntake: React.FC<QuickIntakeProps> = ({ stats, onSubmitUrl }) => {
  const [url, setUrl] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);

  // Segment calculations
  const total = stats.totalTracked || 1;
  const submittedPct = (stats.submitted / total) * 100;
  const needsYouPct = (stats.needsYou / total) * 100;
  const inProgressPct = (stats.inProgress / total) * 100;
  const skippedPct = (stats.skipped / total) * 100;

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!url.trim()) {
      setError('Please provide a valid job posting link.');
      return;
    }

    const trimmed = url.trim().toLowerCase();
    // Validate employer ATS vs forbidden staffing boards
    const forbidden = ['indeed.com', 'linkedin.com/jobs', 'monster.com', 'ziprecruiter.com', 'cybercoders.com'];
    if (forbidden.some((b) => trimmed.includes(b))) {
      setError('Refused: Third-party boards & staffing agencies are blocked by policy. Use direct employer ATS URLs.');
      return;
    }

    setError(null);
    setIsSubmitting(true);
    setTimeout(() => {
      onSubmitUrl(url);
      setUrl('');
      setIsSubmitting(false);
    }, 600);
  };

  return (
    <section className="space-y-4">
      {/* Top Header Metrics & Segmented Progress Bar */}
      <div className="bg-[#131B2E] border border-[#1E293B] rounded-xl p-5 shadow-xl shadow-black/40">
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2 mb-3">
          <div className="flex items-center space-x-2">
            <span className="text-sm font-semibold tracking-wide text-slate-200">System Intake Overview</span>
            <span className="text-xs text-slate-500 font-mono">• Production Run</span>
          </div>
          <div className="text-xs sm:text-sm font-mono font-medium text-slate-300">
            <span className="text-slate-100 font-bold">{stats.totalTracked} tracked</span>
            <span className="text-slate-600 mx-2">·</span>
            <span className="text-emerald-400 font-semibold">{stats.submitted} submitted</span>
            <span className="text-slate-600 mx-2">·</span>
            <span className="text-slate-400">{stats.skipped} skipped</span>
          </div>
        </div>

        {/* Segmented Status Bar */}
        <div className="space-y-1.5">
          <div className="h-3 w-full bg-[#0B0F19] rounded-full overflow-hidden flex p-0.5 border border-[#1E293B]/80">
            {submittedPct > 0 && (
              <div
                style={{ width: `${submittedPct}%` }}
                className="h-full bg-emerald-500 rounded-l-full transition-all duration-500 hover:brightness-110"
                title={`${stats.submitted} Submitted (${submittedPct.toFixed(1)}%)`}
              />
            )}
            {inProgressPct > 0 && (
              <div
                style={{ width: `${inProgressPct}%` }}
                className="h-full bg-indigo-500 transition-all duration-500 hover:brightness-110"
                title={`${stats.inProgress} In Progress`}
              />
            )}
            {needsYouPct > 0 && (
              <div
                style={{ width: `${needsYouPct}%` }}
                className="h-full bg-amber-500 animate-pulse transition-all duration-500"
                title={`${stats.needsYou} Need Attention`}
              />
            )}
            {skippedPct > 0 && (
              <div
                style={{ width: `${skippedPct}%` }}
                className="h-full bg-slate-600 rounded-r-full transition-all duration-500 hover:brightness-110"
                title={`${stats.skipped} Skipped (${skippedPct.toFixed(1)}%)`}
              />
            )}
          </div>

          <div className="flex flex-wrap items-center justify-between text-xs text-slate-400 font-mono pt-1">
            <div className="flex items-center space-x-1.5">
              <span className="h-2 w-2 rounded-full bg-emerald-500 inline-block"></span>
              <span>{stats.submitted} submitted</span>
            </div>
            <div className="flex items-center space-x-1.5">
              <span className="h-2 w-2 rounded-full bg-amber-500 inline-block"></span>
              <span className={stats.needsYou > 0 ? 'text-amber-400 font-semibold' : 'text-slate-500'}>
                {stats.needsYou} need you
              </span>
            </div>
            <div className="flex items-center space-x-1.5">
              <span className="h-2 w-2 rounded-full bg-indigo-500 inline-block"></span>
              <span className={stats.inProgress > 0 ? 'text-indigo-400 font-semibold' : 'text-slate-500'}>
                {stats.inProgress} in progress
              </span>
            </div>
            <div className="flex items-center space-x-1.5">
              <span className="h-2 w-2 rounded-full bg-slate-600 inline-block"></span>
              <span>{stats.skipped} skipped</span>
            </div>
          </div>
        </div>

        {/* Input Form */}
        <form onSubmit={handleSubmit} className="mt-5">
          <div className="flex flex-col sm:flex-row items-stretch gap-2">
            <div className="relative flex-1">
              <div className="absolute inset-y-0 left-0 pl-3.5 flex items-center pointer-events-none text-slate-500">
                <Link2 className="w-4 h-4 text-indigo-400/80" />
              </div>
              <input
                type="url"
                value={url}
                onChange={(e) => {
                  setUrl(e.target.value);
                  if (error) setError(null);
                }}
                placeholder="Paste a job link from the employer's own site (Workday, Greenhouse, Lever...)"
                className="w-full pl-10 pr-4 py-3 bg-[#0B0F19] border border-[#1E293B] rounded-lg text-sm text-slate-100 placeholder-slate-500 focus:outline-none focus:ring-2 focus:ring-indigo-500/50 focus:border-indigo-500 transition-all font-mono"
              />
            </div>
            <button
              type="submit"
              disabled={isSubmitting}
              className="inline-flex items-center justify-center space-x-2 bg-indigo-600 hover:bg-indigo-500 text-white font-medium px-5 py-3 rounded-lg shadow-lg shadow-indigo-500/20 transition-all duration-150 disabled:opacity-60 cursor-pointer text-sm shrink-0 active:scale-[0.99]"
            >
              {isSubmitting ? (
                <>
                  <div className="w-4 h-4 border-2 border-white/20 border-t-white rounded-full animate-spin" />
                  <span>Validating ATS...</span>
                </>
              ) : (
                <>
                  <span>Start application</span>
                  <ArrowRight className="w-4 h-4" />
                </>
              )}
            </button>
          </div>

          {/* Subtext helper & Error messages */}
          <div className="mt-2.5 flex items-start justify-between text-xs text-slate-400">
            {error ? (
              <p className="text-amber-400 flex items-center space-x-1.5 font-medium">
                <ShieldAlert className="w-4 h-4 shrink-0" />
                <span>{error}</span>
              </p>
            ) : (
              <p className="text-slate-400 flex items-center space-x-1.5">
                <span className="text-indigo-400/90 font-mono">✓ Supported:</span>
                <span>Workday, Greenhouse, Lever, iCIMS and other employer sites. Job boards and staffing agencies are refused.</span>
              </p>
            )}
          </div>
        </form>
      </div>
    </section>
  );
};
