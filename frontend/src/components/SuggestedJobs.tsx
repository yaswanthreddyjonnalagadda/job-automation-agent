import React, { useState } from 'react';
import { Sparkles, ExternalLink, Play, X, MapPin } from 'lucide-react';
import { SuggestedJob } from '../types/dashboard';

interface SuggestedJobsProps {
  jobs: SuggestedJob[];
  onStartApplication: (job: SuggestedJob) => void;
  onDismissJob: (id: string) => void;
}

export const SuggestedJobs: React.FC<SuggestedJobsProps> = ({
  jobs,
  onStartApplication,
  onDismissJob,
}) => {
  const [activeFilter, setActiveFilter] = useState<'all' | 'best' | 'remote' | 'greenhouse' | 'lever'>('all');

  const filterOptions = [
    { key: 'all', label: 'All' },
    { key: 'best', label: 'Best matches' },
    { key: 'remote', label: 'Remote' },
    { key: 'greenhouse', label: 'Greenhouse' },
    { key: 'lever', label: 'Lever' },
  ] as const;

  const filteredJobs = jobs.filter((job) => {
    if (activeFilter === 'best') return job.matchPercentage >= 80;
    if (activeFilter === 'remote') return job.tags.includes('Remote') || job.location.toLowerCase().includes('remote');
    if (activeFilter === 'greenhouse') return job.source.toLowerCase() === 'greenhouse';
    if (activeFilter === 'lever') return job.source.toLowerCase() === 'lever';
    return true;
  });

  return (
    <section className="space-y-4">
      {/* Header and Filter Pills */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
        <div className="flex items-center space-x-2">
          <div className="flex items-center space-x-1.5">
            <Sparkles className="w-4 h-4 text-indigo-400" />
            <h2 className="text-base sm:text-lg font-bold text-slate-100">
              Suggested Jobs ({filteredJobs.length})
            </h2>
          </div>
          <span className="text-xs text-slate-500 font-mono">
            autonomously discovered from target boards
          </span>
        </div>

        {/* Filter Pills */}
        <div className="flex flex-wrap items-center gap-1.5">
          {filterOptions.map((filter) => {
            const isActive = activeFilter === filter.key;
            return (
              <button
                key={filter.key}
                onClick={() => setActiveFilter(filter.key)}
                className={`px-3 py-1 rounded-full text-xs font-medium transition-all duration-150 ${
                  isActive
                    ? 'bg-indigo-600/20 text-indigo-300 border border-indigo-500/40 shadow-sm'
                    : 'bg-[#131B2E] text-slate-400 border border-[#1E293B] hover:text-slate-200 hover:border-slate-600'
                }`}
              >
                {filter.label}
              </button>
            );
          })}
        </div>
      </div>

      {/* Compact Grid Feed */}
      {filteredJobs.length === 0 ? (
        <div className="bg-[#131B2E] border border-[#1E293B] rounded-xl p-8 text-center text-slate-400">
          <p className="text-sm">No suggested jobs match the current filter.</p>
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-3.5">
          {filteredJobs.map((job) => (
            <div
              key={job.id}
              className="group bg-[#131B2E] border border-[#1E293B] hover:border-slate-700 rounded-xl p-4 transition-all duration-200 flex flex-col justify-between shadow-lg shadow-black/20"
            >
              <div className="space-y-2.5">
                {/* Top Row: Match Pill + Company + External Link */}
                <div className="flex items-start justify-between gap-3">
                  <div className="flex items-center space-x-2.5">
                    {/* Match percentage badge */}
                    <div
                      className={`px-2.5 py-1 rounded-full text-xs font-mono font-bold border ${
                        job.matchPercentage >= 80
                          ? 'bg-emerald-500/10 text-emerald-400 border-emerald-500/20'
                          : job.matchPercentage >= 70
                          ? 'bg-indigo-500/10 text-indigo-300 border-indigo-500/20'
                          : 'bg-slate-500/10 text-slate-300 border-slate-500/20'
                      }`}
                    >
                      {job.matchPercentage}% match
                    </div>

                    <span className="text-xs font-semibold uppercase tracking-wider text-slate-400">
                      {job.company}
                    </span>
                  </div>

                  <a
                    href={job.url}
                    target="_blank"
                    rel="noreferrer"
                    className="text-slate-500 hover:text-slate-200 transition-colors p-1"
                    title="Open posting"
                  >
                    <ExternalLink className="w-4 h-4" />
                  </a>
                </div>

                {/* Role Title */}
                <div>
                  <a
                    href={job.url}
                    target="_blank"
                    rel="noreferrer"
                    className="text-sm font-semibold text-slate-100 group-hover:text-indigo-300 transition-colors inline-block"
                  >
                    {job.role}
                  </a>
                  <div className="flex items-center text-xs text-slate-500 mt-1 font-mono">
                    <MapPin className="w-3 h-3 mr-1 text-slate-600" />
                    <span>{job.location}</span>
                  </div>
                </div>

                {/* Inline Metadata Chips / Tags */}
                <div className="flex flex-wrap gap-1.5 pt-1">
                  {job.tags.map((tag) => (
                    <span
                      key={tag}
                      className="px-2 py-0.5 rounded text-[11px] font-mono bg-[#0B0F19] text-slate-400 border border-[#1E293B]"
                    >
                      {tag}
                    </span>
                  ))}
                </div>
              </div>

              {/* Action Buttons per card */}
              <div className="mt-4 pt-3 border-t border-[#1E293B]/60 flex items-center justify-between gap-2">
                <button
                  onClick={() => onDismissJob(job.id)}
                  className="flex items-center space-x-1 text-xs text-slate-500 hover:text-rose-400 font-medium px-2 py-1.5 rounded transition-colors"
                >
                  <X className="w-3.5 h-3.5" />
                  <span>Not interested</span>
                </button>

                <button
                  onClick={() => onStartApplication(job)}
                  className="inline-flex items-center space-x-1.5 bg-indigo-600 hover:bg-indigo-500 text-white text-xs font-semibold px-3 py-1.5 rounded-lg shadow-md shadow-indigo-500/20 transition-all duration-150 active:scale-[0.98]"
                >
                  <Play className="w-3 h-3 fill-white" />
                  <span>Start application</span>
                </button>
              </div>
            </div>
          ))}
        </div>
      )}
    </section>
  );
};
