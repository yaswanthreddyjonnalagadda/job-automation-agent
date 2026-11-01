import React, { useState } from 'react';
import { Terminal, RefreshCw, ExternalLink, Radio, Cpu } from 'lucide-react';
import { ActiveMonitorTask } from '../types/dashboard';

interface LiveMonitorProps {
  task: ActiveMonitorTask;
  onViewLog: () => void;
  onReloadCode: () => void;
}

export const LiveMonitor: React.FC<LiveMonitorProps> = ({ task, onViewLog, onReloadCode }) => {
  const [isReloading, setIsReloading] = useState(false);
  const [reloadSuccess, setReloadSuccess] = useState(false);

  const handleReload = () => {
    setIsReloading(true);
    setTimeout(() => {
      onReloadCode();
      setIsReloading(false);
      setReloadSuccess(true);
      setTimeout(() => setReloadSuccess(false), 2500);
    }, 800);
  };

  return (
    <div className="relative overflow-hidden bg-gradient-to-r from-[#131B2E] via-[#131B2E] to-[#151f38] border border-indigo-500/30 rounded-xl p-5 shadow-2xl shadow-indigo-950/30">
      {/* Decorative top ambient bar */}
      <div className="absolute top-0 left-0 right-0 h-[2px] bg-gradient-to-r from-transparent via-indigo-500 to-transparent animate-pulse" />

      <div className="flex flex-col lg:flex-row lg:items-center justify-between gap-4">
        {/* Left Status Info */}
        <div className="space-y-2">
          <div className="flex flex-wrap items-center gap-2.5">
            <span className="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-mono font-semibold bg-emerald-500/10 text-emerald-400 border border-emerald-500/20">
              <span className="relative flex h-2 w-2 mr-1.5">
                <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-75"></span>
                <span className="relative inline-flex rounded-full h-2 w-2 bg-emerald-500"></span>
              </span>
              RUNNING TASK
            </span>

            <span className="text-xs text-slate-400 font-mono flex items-center">
              <Radio className="w-3.5 h-3.5 mr-1 text-indigo-400 animate-pulse" />
              {task.startedAt}
            </span>

            <span className="text-xs text-slate-500 hidden sm:inline">• Session ID: #624</span>
          </div>

          <div>
            <div className="flex items-center space-x-2">
              <h3 className="text-base sm:text-lg font-bold text-slate-100 tracking-tight">
                {task.company}
              </h3>
              <span className="text-slate-500">—</span>
              <span className="text-base sm:text-lg font-medium text-indigo-300">
                {task.title}
              </span>
              <a
                href={task.url}
                target="_blank"
                rel="noreferrer"
                className="text-slate-500 hover:text-slate-300 transition-colors inline-flex items-center ml-1"
                title="Open job URL in new tab"
              >
                <ExternalLink className="w-3.5 h-3.5" />
              </a>
            </div>

            <p className="text-xs font-mono text-slate-400 mt-1 flex items-center space-x-1.5">
              <span className="text-indigo-400">Current Phase:</span>
              <span className="text-slate-300">{task.currentStep}</span>
            </p>
          </div>
        </div>

        {/* Right Action Controls */}
        <div className="flex items-center space-x-2.5 shrink-0 pt-2 lg:pt-0">
          <button
            onClick={onViewLog}
            className="flex items-center space-x-2 bg-[#0B0F19] hover:bg-[#1E293B] border border-[#1E293B] hover:border-slate-600 px-3.5 py-2 rounded-lg text-xs font-mono font-medium text-slate-200 transition-all duration-150 shadow-sm"
          >
            <Terminal className="w-4 h-4 text-emerald-400" />
            <span>View log</span>
          </button>

          <button
            onClick={handleReload}
            disabled={isReloading}
            className="flex items-center space-x-2 bg-[#0B0F19] hover:bg-[#1E293B] border border-[#1E293B] hover:border-indigo-500/50 px-3.5 py-2 rounded-lg text-xs font-mono font-medium text-slate-300 transition-all duration-150 shadow-sm disabled:opacity-50"
          >
            <RefreshCw className={`w-3.5 h-3.5 text-indigo-400 ${isReloading ? 'animate-spin' : ''}`} />
            <span>{isReloading ? 'Reloading...' : reloadSuccess ? 'Reloaded ✓' : 'Reload agent code'}</span>
          </button>
        </div>
      </div>

      {/* Mini terminal preview line */}
      <div className="mt-3.5 pt-3 border-t border-[#1E293B]/70 flex items-center justify-between text-[11px] font-mono text-slate-400">
        <div className="truncate flex items-center space-x-2">
          <span className="text-emerald-400 select-none">➜</span>
          <span className="text-slate-300 truncate">
            {task.logLines[task.logLines.length - 1] || 'Agent inspecting DOM elements...'}
          </span>
        </div>
        <div className="hidden sm:flex items-center space-x-2 shrink-0 text-slate-500 pl-3">
          <Cpu className="w-3.5 h-3.5 text-indigo-400/70" />
          <span>Interactive Desktop attached</span>
        </div>
      </div>
    </div>
  );
};
