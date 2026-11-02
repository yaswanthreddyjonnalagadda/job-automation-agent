import React, { useState } from 'react';
import { X, Terminal, Copy, Check } from 'lucide-react';
import { ActiveMonitorTask } from '../types/dashboard';

interface LogModalProps {
  isOpen: boolean;
  onClose: () => void;
  task: ActiveMonitorTask;
}

export const LogModal: React.FC<LogModalProps> = ({ isOpen, onClose, task }) => {
  const [copied, setCopied] = useState(false);

  if (!isOpen) return null;

  const fullLogs = task.logLines.join('\n');

  const handleCopy = () => {
    navigator.clipboard.writeText(fullLogs);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/80 backdrop-blur-sm animate-in fade-in duration-200">
      <div className="relative w-full max-w-3xl bg-[#0B0F19] border border-[#1E293B] rounded-xl shadow-2xl overflow-hidden flex flex-col max-h-[85vh]">
        {/* Modal Header */}
        <div className="px-5 py-4 border-b border-[#1E293B] flex items-center justify-between bg-[#131B2E]">
          <div className="flex items-center space-x-2.5">
            <div className="p-1.5 rounded-md bg-emerald-500/10 text-emerald-400 border border-emerald-500/20">
              <Terminal className="w-4 h-4" />
            </div>
            <div>
              <h3 className="text-sm font-bold text-slate-100">Live Agent Execution Log</h3>
              <p className="text-xs font-mono text-slate-400">
                {task.company} — {task.title}
              </p>
            </div>
          </div>

          <div className="flex items-center space-x-2">
            <button
              onClick={handleCopy}
              className="flex items-center space-x-1.5 px-3 py-1.5 bg-[#0B0F19] hover:bg-[#1E293B] border border-[#1E293B] rounded-lg text-xs font-mono text-slate-300 transition-colors"
              title="Copy execution log"
            >
              {copied ? <Check className="w-3.5 h-3.5 text-emerald-400" /> : <Copy className="w-3.5 h-3.5" />}
              <span>{copied ? 'Copied' : 'Copy log'}</span>
            </button>
            <button
              onClick={onClose}
              className="p-1.5 text-slate-400 hover:text-slate-100 hover:bg-[#1E293B] rounded-lg transition-colors"
            >
              <X className="w-5 h-5" />
            </button>
          </div>
        </div>

        {/* Terminal Body */}
        <div className="p-4 overflow-y-auto font-mono text-xs text-slate-300 space-y-1.5 bg-[#070A11] flex-1">
          <div className="text-slate-500 pb-2 border-b border-[#1E293B]/60 mb-2">
            # Session initiated via Playwright runtime [WinSta0\Default]
          </div>
          {task.logLines.map((line, idx) => (
            <div key={idx} className="flex items-start space-x-2">
              <span className="text-slate-600 select-none w-6 text-right shrink-0">
                {idx + 1}
              </span>
              <span className={line.includes('assertion') || line.includes('Finished') ? 'text-emerald-400 font-semibold' : 'text-slate-300'}>
                {line}
              </span>
            </div>
          ))}
          <div className="pt-2 text-indigo-400/80 animate-pulse flex items-center space-x-2">
            <span>➜</span>
            <span>Awaiting server response...</span>
          </div>
        </div>

        {/* Modal Footer */}
        <div className="px-5 py-3 border-t border-[#1E293B] bg-[#131B2E] flex items-center justify-between text-xs font-mono text-slate-400">
          <span className="flex items-center space-x-1.5">
            <span className="h-2 w-2 rounded-full bg-emerald-500"></span>
            <span>Process live • pid 20384</span>
          </span>
          <button
            onClick={onClose}
            className="px-3 py-1 bg-indigo-600 hover:bg-indigo-500 text-white rounded font-sans font-medium text-xs transition-colors"
          >
            Close
          </button>
        </div>
      </div>
    </div>
  );
};
