import React, { useState } from 'react';
import { CheckCircle2, Activity, ShieldCheck, User, Settings, FileText } from 'lucide-react';

interface NavbarProps {
  activeTab?: string;
  onTabChange?: (tab: string) => void;
}

export const Navbar: React.FC<NavbarProps> = ({ 
  activeTab = 'Applications', 
  onTabChange 
}) => {
  const [currentTab, setCurrentTab] = useState(activeTab);

  const navItems = [
    { name: 'Applications', icon: CheckCircle2 },
    { name: 'Progress', icon: Activity },
    { name: 'Profile', icon: User },
    { name: 'Saved answers', icon: FileText },
    { name: 'Settings', icon: Settings },
    { name: 'System Health', icon: ShieldCheck },
  ];

  const handleNavClick = (tabName: string) => {
    setCurrentTab(tabName);
    onTabChange?.(tabName);
  };

  return (
    <header className="sticky top-0 z-40 w-full border-b border-[#1E293B] bg-[#0B0F19]/90 backdrop-blur-md">
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
        <div className="flex items-center justify-between h-16">
          {/* Brand */}
          <div className="flex items-center space-x-3">
            <div className="h-9 w-9 rounded-lg bg-indigo-600/20 border border-indigo-500/30 flex items-center justify-center text-indigo-400 shadow-sm shadow-indigo-500/10">
              <CheckCircle2 className="w-5 h-5 text-indigo-400 stroke-[2.5]" />
            </div>
            <div className="flex items-baseline space-x-2">
              <span className="text-lg font-bold tracking-tight text-slate-100">Job Agent</span>
              <span className="text-xs font-mono font-medium text-indigo-400 bg-indigo-500/10 px-2 py-0.5 rounded-full border border-indigo-500/20">
                v2.4 CI/CD
              </span>
            </div>
          </div>

          {/* Desktop Navigation Links */}
          <nav className="hidden md:flex items-center space-x-1 lg:space-x-2">
            {navItems.map((item) => {
              const isActive = currentTab === item.name;
              const Icon = item.icon;
              return (
                <button
                  key={item.name}
                  onClick={() => handleNavClick(item.name)}
                  className={`flex items-center space-x-2 px-3 py-1.5 rounded-md text-sm font-medium transition-all duration-150 ${
                    isActive
                      ? 'bg-indigo-600/15 text-indigo-300 border border-indigo-500/30 shadow-sm shadow-indigo-500/10'
                      : 'text-slate-400 hover:text-slate-200 hover:bg-[#131B2E]'
                  }`}
                >
                  <Icon className={`w-4 h-4 ${isActive ? 'text-indigo-400' : 'text-slate-500'}`} />
                  <span>{item.name}</span>
                </button>
              );
            })}
          </nav>

          {/* Right Status / Profile Indicator */}
          <div className="flex items-center space-x-3">
            <div className="flex items-center space-x-2 bg-[#131B2E] border border-[#1E293B] px-3 py-1.5 rounded-lg text-xs font-mono">
              <span className="relative flex h-2 w-2">
                <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-75"></span>
                <span className="relative inline-flex rounded-full h-2 w-2 bg-emerald-500"></span>
              </span>
              <span className="text-slate-300 font-medium">Agent Active</span>
              <span className="text-slate-500 hidden sm:inline">| port 5000</span>
            </div>

            <div className="h-8 w-8 rounded-full bg-indigo-600/30 border border-indigo-500/40 flex items-center justify-center text-xs font-bold text-indigo-200">
              YJ
            </div>
          </div>
        </div>
      </div>
    </header>
  );
};
