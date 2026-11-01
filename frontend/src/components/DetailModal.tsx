import React from 'react';
import { X, ExternalLink, FileText, MapPin } from 'lucide-react';
import { Application } from '../types/dashboard';

interface DetailModalProps {
  application: Application | null;
  onClose: () => void;
}

export const DetailModal: React.FC<DetailModalProps> = ({ application, onClose }) => {
  if (!application) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/80 backdrop-blur-sm animate-in fade-in duration-200">
      <div className="relative w-full max-w-2xl bg-[#0B0F19] border border-[#1E293B] rounded-xl shadow-2xl overflow-hidden flex flex-col">
        {/* Header */}
        <div className="px-6 py-4 border-b border-[#1E293B] flex items-center justify-between bg-[#131B2E]">
          <div>
            <div className="flex items-center space-x-2">
              <span className="text-xs font-mono font-semibold text-indigo-400 bg-indigo-500/10 px-2 py-0.5 rounded border border-indigo-500/20">
                #{application.id}
              </span>
              <h3 className="text-base font-bold text-slate-100">{application.company}</h3>
            </div>
            <p className="text-xs text-slate-400 mt-0.5">{application.role}</p>
          </div>
          <button
            onClick={onClose}
            className="p-1.5 text-slate-400 hover:text-slate-100 hover:bg-[#1E293B] rounded-lg transition-colors"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Content */}
        <div className="p-6 space-y-4 overflow-y-auto max-h-[70vh] text-sm">
          <div className="grid grid-cols-2 gap-4">
            <div className="bg-[#131B2E] p-3 rounded-lg border border-[#1E293B]">
              <span className="text-xs font-mono text-slate-500 block">Status</span>
              <span className="font-semibold text-slate-200 mt-1 inline-block">
                {application.statusDetail}
              </span>
            </div>
            <div className="bg-[#131B2E] p-3 rounded-lg border border-[#1E293B]">
              <span className="text-xs font-mono text-slate-500 block">ATS Provider</span>
              <span className="font-semibold text-slate-200 mt-1 inline-block">
                {application.source}
              </span>
            </div>
          </div>

          <div className="space-y-1">
            <label className="text-xs font-mono text-slate-400">Application URL</label>
            <div className="flex items-center justify-between bg-[#070A11] p-3 rounded-lg border border-[#1E293B]">
              <span className="text-xs font-mono text-indigo-300 truncate mr-3">
                {application.url}
              </span>
              <a
                href={application.url}
                target="_blank"
                rel="noreferrer"
                className="text-slate-400 hover:text-white shrink-0"
              >
                <ExternalLink className="w-4 h-4" />
              </a>
            </div>
          </div>

          <div className="space-y-1">
            <label className="text-xs font-mono text-slate-400">Location</label>
            <div className="bg-[#131B2E] p-3 rounded-lg border border-[#1E293B] text-slate-200 flex items-center space-x-2">
              <MapPin className="w-4 h-4 text-indigo-400" />
              <span>{application.location}</span>
            </div>
          </div>

          {application.resumeUsed && (
            <div className="space-y-1">
              <label className="text-xs font-mono text-slate-400">Tailored Resume Sent</label>
              <div className="bg-[#131B2E] p-3 rounded-lg border border-[#1E293B] flex items-center space-x-2 text-slate-200">
                <FileText className="w-4 h-4 text-emerald-400" />
                <span className="font-mono text-xs">{application.resumeUsed}</span>
              </div>
            </div>
          )}

          {application.notes && (
            <div className="space-y-1">
              <label className="text-xs font-mono text-slate-400">Agent Notes & Telemetry</label>
              <div className="bg-[#070A11] p-3 rounded-lg border border-[#1E293B] text-xs font-mono text-slate-300">
                {application.notes}
              </div>
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="px-6 py-3 border-t border-[#1E293B] bg-[#131B2E] flex justify-end">
          <button
            onClick={onClose}
            className="px-4 py-2 bg-indigo-600 hover:bg-indigo-500 text-white rounded-lg text-xs font-semibold shadow-lg shadow-indigo-500/20 transition-all cursor-pointer"
          >
            Done
          </button>
        </div>
      </div>
    </div>
  );
};
