import React, { useState } from 'react';
import {
  Search,
  MoreVertical,
  ExternalLink,
  CheckCircle,
  AlertCircle,
  FileText,
  Trash2,
  CheckCheck,
} from 'lucide-react';
import { Application, ApplicationStatus } from '../types/dashboard';

interface ApplicationTableProps {
  applications: Application[];
  onViewDetails: (app: Application) => void;
  onDeleteApplication: (id: number) => void;
  onMarkSubmitted: (id: number) => void;
}

export const ApplicationTable: React.FC<ApplicationTableProps> = ({
  applications,
  onViewDetails,
  onDeleteApplication,
  onMarkSubmitted,
}) => {
  const [activeTab, setActiveTab] = useState<'all' | 'needs_you' | 'in_progress' | 'submitted' | 'skipped'>('all');
  const [searchQuery, setSearchQuery] = useState('');
  const [openMenuId, setOpenMenuId] = useState<number | null>(null);
  const [visibleCount, setVisibleCount] = useState(10);

  // Status counts
  const submittedCount = applications.filter((a) => a.status === 'submitted').length;
  const skippedCount = applications.filter((a) => a.status === 'skipped').length;
  const needsYouCount = applications.filter((a) => a.status === 'needs_attention').length;
  const inProgressCount = applications.filter((a) => a.status === 'in_progress').length;

  const tabs = [
    { key: 'all', label: 'All', count: applications.length },
    { key: 'needs_you', label: 'Needs you', count: needsYouCount },
    { key: 'in_progress', label: 'In progress', count: inProgressCount },
    { key: 'submitted', label: 'Submitted', count: submittedCount },
    { key: 'skipped', label: 'Skipped', count: skippedCount },
  ] as const;

  const filteredApps = applications.filter((app) => {
    // Status tab filter
    if (activeTab === 'needs_you' && app.status !== 'needs_attention') return false;
    if (activeTab === 'in_progress' && app.status !== 'in_progress') return false;
    if (activeTab === 'submitted' && app.status !== 'submitted') return false;
    if (activeTab === 'skipped' && app.status !== 'skipped') return false;

    // Search query filter
    if (searchQuery.trim()) {
      const q = searchQuery.toLowerCase();
      const match =
        app.company.toLowerCase().includes(q) ||
        app.role.toLowerCase().includes(q) ||
        app.location.toLowerCase().includes(q) ||
        app.source.toLowerCase().includes(q);
      if (!match) return false;
    }

    return true;
  });

  const displayedApps = filteredApps.slice(0, visibleCount);

  const getStatusBadge = (status: ApplicationStatus, detail: string) => {
    switch (status) {
      case 'submitted':
        return (
          <span className="inline-flex items-center px-2.5 py-1 rounded-full text-xs font-mono font-medium bg-emerald-500/10 text-emerald-400 border border-emerald-500/20">
            <CheckCircle className="w-3.5 h-3.5 mr-1" />
            {detail || 'Submitted'}
          </span>
        );
      case 'skipped':
        return (
          <span className="inline-flex items-center px-2.5 py-1 rounded-full text-xs font-mono font-medium bg-slate-500/10 text-slate-400 border border-slate-500/20">
            {detail || 'Skipped'}
          </span>
        );
      case 'needs_attention':
        return (
          <span className="inline-flex items-center px-2.5 py-1 rounded-full text-xs font-mono font-medium bg-amber-500/10 text-amber-400 border border-amber-500/20 animate-pulse">
            <AlertCircle className="w-3.5 h-3.5 mr-1" />
            {detail || 'Needs attention'}
          </span>
        );
      case 'in_progress':
        return (
          <span className="inline-flex items-center px-2.5 py-1 rounded-full text-xs font-mono font-medium bg-indigo-500/10 text-indigo-400 border border-indigo-500/20">
            <span className="h-1.5 w-1.5 rounded-full bg-indigo-400 mr-1.5 animate-ping" />
            In progress
          </span>
        );
    }
  };

  return (
    <section className="bg-[#131B2E] border border-[#1E293B] rounded-xl overflow-hidden shadow-2xl shadow-black/40">
      {/* Header controls: Filter Tabs & Search Bar */}
      <div className="p-4 sm:p-5 border-b border-[#1E293B] flex flex-col md:flex-row md:items-center justify-between gap-4">
        {/* Filter Tabs */}
        <div className="flex flex-wrap items-center gap-1.5 bg-[#0B0F19] p-1 rounded-lg border border-[#1E293B]">
          {tabs.map((tab) => {
            const isActive = activeTab === tab.key;
            return (
              <button
                key={tab.key}
                onClick={() => {
                  setActiveTab(tab.key);
                  setVisibleCount(10);
                }}
                className={`flex items-center space-x-1.5 px-3 py-1.5 rounded-md text-xs font-medium transition-all duration-150 ${
                  isActive
                    ? 'bg-[#131B2E] text-slate-100 font-semibold border border-[#1E293B] shadow-sm'
                    : 'text-slate-400 hover:text-slate-200'
                }`}
              >
                <span>{tab.label}</span>
                <span
                  className={`text-[11px] font-mono px-1.5 py-0.2 rounded-full ${
                    isActive
                      ? 'bg-indigo-500/20 text-indigo-300'
                      : 'bg-slate-800 text-slate-500'
                  }`}
                >
                  {tab.count}
                </span>
              </button>
            );
          })}
        </div>

        {/* Search Bar */}
        <div className="relative w-full md:w-72">
          <div className="absolute inset-y-0 left-0 pl-3 flex items-center pointer-events-none text-slate-500">
            <Search className="w-4 h-4" />
          </div>
          <input
            type="text"
            value={searchQuery}
            onChange={(e) => {
              setSearchQuery(e.target.value);
              setVisibleCount(10);
            }}
            placeholder="Search company or role"
            className="w-full pl-9 pr-3 py-1.5 bg-[#0B0F19] border border-[#1E293B] rounded-lg text-xs font-mono text-slate-200 placeholder-slate-500 focus:outline-none focus:ring-1 focus:ring-indigo-500 focus:border-indigo-500 transition-all"
          />
        </div>
      </div>

      {/* Table Content */}
      <div className="overflow-x-auto">
        <table className="w-full text-left text-sm text-slate-300">
          <thead className="bg-[#0B0F19]/60 text-xs font-mono uppercase tracking-wider text-slate-400 border-b border-[#1E293B]">
            <tr>
              <th scope="col" className="py-3 px-4 sm:px-6">Company & Role</th>
              <th scope="col" className="py-3 px-4 hidden sm:table-cell">Location & ATS</th>
              <th scope="col" className="py-3 px-4">Status</th>
              <th scope="col" className="py-3 px-4 hidden lg:table-cell">Timestamp</th>
              <th scope="col" className="py-3 px-4 text-right">Actions</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-[#1E293B]/70 font-sans">
            {displayedApps.length === 0 ? (
              <tr>
                <td colSpan={5} className="py-12 text-center text-slate-500 text-sm">
                  No applications found matching the selected criteria.
                </td>
              </tr>
            ) : (
              displayedApps.map((app) => (
                <tr
                  key={app.id}
                  className="hover:bg-[#1A243D]/60 transition-colors group"
                >
                  {/* Company & Role Title */}
                  <td className="py-3.5 px-4 sm:px-6">
                    <div className="flex flex-col">
                      <div className="flex items-center space-x-2">
                        <span className="font-semibold text-slate-100 group-hover:text-indigo-300 transition-colors">
                          {app.company}
                        </span>
                        <a
                          href={app.url}
                          target="_blank"
                          rel="noreferrer"
                          className="text-slate-500 hover:text-slate-300 transition-colors"
                          title="Open application link"
                        >
                          <ExternalLink className="w-3.5 h-3.5" />
                        </a>
                      </div>
                      <span className="text-xs text-slate-400 line-clamp-1 mt-0.5">
                        {app.role}
                      </span>
                    </div>
                  </td>

                  {/* Location & Source */}
                  <td className="py-3.5 px-4 hidden sm:table-cell">
                    <div className="flex flex-col text-xs font-mono text-slate-400">
                      <span>{app.location}</span>
                      <span className="text-slate-500 text-[11px] mt-0.5 font-sans">
                        via {app.source}
                      </span>
                    </div>
                  </td>

                  {/* Status Badge */}
                  <td className="py-3.5 px-4">
                    {getStatusBadge(app.status, app.statusDetail)}
                  </td>

                  {/* Timestamp */}
                  <td className="py-3.5 px-4 hidden lg:table-cell text-xs font-mono text-slate-400">
                    {app.timestamp}
                  </td>

                  {/* Row Action Menu */}
                  <td className="py-3.5 px-4 text-right relative">
                    <div className="flex items-center justify-end space-x-1">
                      <button
                        onClick={() => onViewDetails(app)}
                        className="px-2.5 py-1 text-xs font-medium text-slate-300 hover:text-white bg-[#0B0F19] hover:bg-[#1E293B] border border-[#1E293B] rounded-md transition-all shadow-sm"
                      >
                        Details
                      </button>

                      {/* Dropdown menu trigger */}
                      <div className="relative inline-block text-left">
                        <button
                          onClick={() => setOpenMenuId(openMenuId === app.id ? null : app.id)}
                          className="p-1.5 text-slate-400 hover:text-slate-200 hover:bg-[#0B0F19] rounded-md transition-colors"
                          title="More options"
                        >
                          <MoreVertical className="w-4 h-4" />
                        </button>

                        {openMenuId === app.id && (
                          <>
                            {/* Backdrop click dismiss */}
                            <div
                              className="fixed inset-0 z-20"
                              onClick={() => setOpenMenuId(null)}
                            />

                            <div className="origin-top-right absolute right-0 mt-2 w-48 rounded-lg shadow-2xl bg-[#0B0F19] border border-[#1E293B] divide-y divide-[#1E293B] z-30 font-sans text-xs">
                              <div className="py-1">
                                <button
                                  onClick={() => {
                                    onViewDetails(app);
                                    setOpenMenuId(null);
                                  }}
                                  className="w-full text-left px-4 py-2 text-slate-300 hover:bg-[#131B2E] flex items-center space-x-2"
                                >
                                  <FileText className="w-3.5 h-3.5 text-slate-400" />
                                  <span>View submission details</span>
                                </button>
                                {app.status !== 'submitted' && (
                                  <button
                                    onClick={() => {
                                      onMarkSubmitted(app.id);
                                      setOpenMenuId(null);
                                    }}
                                    className="w-full text-left px-4 py-2 text-emerald-400 hover:bg-[#131B2E] flex items-center space-x-2"
                                  >
                                    <CheckCheck className="w-3.5 h-3.5" />
                                    <span>Mark as submitted</span>
                                  </button>
                                )}
                              </div>
                              <div className="py-1">
                                <button
                                  onClick={() => {
                                    onDeleteApplication(app.id);
                                    setOpenMenuId(null);
                                  }}
                                  className="w-full text-left px-4 py-2 text-rose-400 hover:bg-[#131B2E] flex items-center space-x-2"
                                >
                                  <Trash2 className="w-3.5 h-3.5" />
                                  <span>Delete entry</span>
                                </button>
                              </div>
                            </div>
                          </>
                        )}
                      </div>
                    </div>
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>

      {/* Pagination / Load More Footer */}
      <div className="p-4 bg-[#0B0F19]/40 border-t border-[#1E293B] flex flex-col sm:flex-row items-center justify-between gap-3 text-xs font-mono text-slate-400">
        <div>
          Showing <span className="text-slate-200 font-semibold">{Math.min(displayedApps.length, visibleCount)}</span> of{' '}
          <span className="text-slate-200 font-semibold">{filteredApps.length}</span> entries
          {filteredApps.length !== applications.length && (
            <span className="text-slate-500 ml-1">({applications.length} total)</span>
          )}
        </div>

        {visibleCount < filteredApps.length && (
          <button
            onClick={() => setVisibleCount((prev) => prev + 10)}
            className="px-4 py-1.5 bg-[#0B0F19] hover:bg-[#1E293B] border border-[#1E293B] hover:border-slate-600 rounded-lg text-slate-200 font-medium transition-all shadow-sm active:scale-95 cursor-pointer"
          >
            Show 10 more
          </button>
        )}
      </div>
    </section>
  );
};
