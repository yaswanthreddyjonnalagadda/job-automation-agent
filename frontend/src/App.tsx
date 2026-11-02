import React, { useState } from 'react';
import { Navbar } from './components/Navbar';
import { QuickIntake } from './components/QuickIntake';
import { LiveMonitor } from './components/LiveMonitor';
import { SuggestedJobs } from './components/SuggestedJobs';
import { ApplicationTable } from './components/ApplicationTable';
import { LogModal } from './components/LogModal';
import { DetailModal } from './components/DetailModal';
import {
  initialStats,
  activeTask,
  mockSuggestedJobs,
  mockApplications,
} from './data/mockData';
import { Application, SuggestedJob } from './types/dashboard';
import { CheckCircle2 } from 'lucide-react';

export const App: React.FC = () => {
  const [stats, setStats] = useState(initialStats);
  const [applications, setApplications] = useState<Application[]>(mockApplications);
  const [suggestedJobs, setSuggestedJobs] = useState<SuggestedJob[]>(mockSuggestedJobs);
  const [currentTask] = useState(activeTask);
  
  // Modals state
  const [isLogModalOpen, setIsLogModalOpen] = useState(false);
  const [selectedApp, setSelectedApp] = useState<Application | null>(null);

  // Toast notifications
  const [toastMessage, setToastMessage] = useState<string | null>(null);

  const showToast = (msg: string) => {
    setToastMessage(msg);
    setTimeout(() => {
      setToastMessage(null);
    }, 3500);
  };

  // Intake URL submission
  const handleUrlSubmit = (url: string) => {
    const newId = (applications[0]?.id || 82) + 1;
    const newApp: Application = {
      id: newId,
      company: 'New Intake Company',
      role: 'Staff Infrastructure / Network Engineer',
      location: 'Remote, US',
      source: url.includes('greenhouse') ? 'Greenhouse' : url.includes('lever') ? 'Lever' : 'Workday',
      url,
      status: 'in_progress',
      statusDetail: 'In progress (Step 1)',
      timestamp: 'Just now',
    };

    setApplications([newApp, ...applications]);
    setStats((prev) => ({
      ...prev,
      totalTracked: prev.totalTracked + 1,
      inProgress: prev.inProgress + 1,
    }));

    showToast(`Started application for ${newApp.company} (${newApp.source})`);
  };

  // Suggested Job application start
  const handleStartSuggestedJob = (job: SuggestedJob) => {
    const newId = (applications[0]?.id || 82) + 1;
    const newApp: Application = {
      id: newId,
      company: job.company,
      role: job.role,
      location: job.location,
      source: job.source,
      url: job.url,
      status: 'in_progress',
      statusDetail: 'In progress (Initializing runner)',
      timestamp: 'Just now',
    };

    setApplications([newApp, ...applications]);
    setSuggestedJobs(suggestedJobs.filter((j) => j.id !== job.id));
    setStats((prev) => ({
      ...prev,
      totalTracked: prev.totalTracked + 1,
      inProgress: prev.inProgress + 1,
    }));

    showToast(`Launched application for ${job.company}: ${job.role}`);
  };

  // Suggested Job dismissal
  const handleDismissJob = (id: string) => {
    const dismissed = suggestedJobs.find((j) => j.id === id);
    setSuggestedJobs(suggestedJobs.filter((j) => j.id !== id));
    if (dismissed) {
      showToast(`Dismissed ${dismissed.company} from suggested feed`);
    }
  };

  // Delete application
  const handleDeleteApplication = (id: number) => {
    const target = applications.find((a) => a.id === id);
    setApplications(applications.filter((a) => a.id !== id));
    setStats((prev) => ({
      ...prev,
      totalTracked: Math.max(0, prev.totalTracked - 1),
      submitted: target?.status === 'submitted' ? prev.submitted - 1 : prev.submitted,
      skipped: target?.status === 'skipped' ? prev.skipped - 1 : prev.skipped,
      needsYou: target?.status === 'needs_attention' ? prev.needsYou - 1 : prev.needsYou,
      inProgress: target?.status === 'in_progress' ? prev.inProgress - 1 : prev.inProgress,
    }));
    showToast(`Deleted application #${id}`);
  };

  // Mark application submitted
  const handleMarkSubmitted = (id: number) => {
    setApplications(
      applications.map((app) =>
        app.id === id
          ? { ...app, status: 'submitted', statusDetail: '✓ Submitted (manual confirmation)' }
          : app
      )
    );
    setStats((prev) => ({
      ...prev,
      submitted: prev.submitted + 1,
      needsYou: Math.max(0, prev.needsYou - 1),
    }));
    showToast(`Application #${id} marked as submitted`);
  };

  // Reload code simulation
  const handleReloadCode = () => {
    showToast('Code reloaded: Playwright runner & ATS page agents hot-swapped');
  };

  return (
    <div className="min-h-screen bg-[#0B0F19] text-slate-100 flex flex-col font-sans selection:bg-indigo-500/30 selection:text-indigo-200">
      {/* A. Top Navigation Bar */}
      <Navbar activeTab="Applications" />

      {/* Main Content Area */}
      <main className="flex-1 max-w-7xl w-full mx-auto px-4 sm:px-6 lg:px-8 py-6 space-y-6">
        {/* B. Header & Quick Intake Bar */}
        <QuickIntake stats={stats} onSubmitUrl={handleUrlSubmit} />

        {/* C. Live Execution Banner */}
        <LiveMonitor
          task={currentTask}
          onViewLog={() => setIsLogModalOpen(true)}
          onReloadCode={handleReloadCode}
        />

        {/* D. Suggested Jobs Section */}
        <SuggestedJobs
          jobs={suggestedJobs}
          onStartApplication={handleStartSuggestedJob}
          onDismissJob={handleDismissJob}
        />

        {/* E. Your Applications Table / Log */}
        <ApplicationTable
          applications={applications}
          onViewDetails={(app) => setSelectedApp(app)}
          onDeleteApplication={handleDeleteApplication}
          onMarkSubmitted={handleMarkSubmitted}
        />
      </main>

      {/* Footer */}
      <footer className="border-t border-[#1E293B] bg-[#070A11] py-4 text-center text-xs font-mono text-slate-500">
        <p>Job Agent UI & Automation Engine • Loopback only (127.0.0.1) • Confidential Candidate Profile</p>
      </footer>

      {/* Modals */}
      <LogModal
        isOpen={isLogModalOpen}
        onClose={() => setIsLogModalOpen(false)}
        task={currentTask}
      />

      <DetailModal
        application={selectedApp}
        onClose={() => setSelectedApp(null)}
      />

      {/* Floating Toast Notification */}
      {toastMessage && (
        <div className="fixed bottom-5 right-5 z-50 flex items-center space-x-2.5 bg-[#131B2E] border border-indigo-500/40 text-slate-100 px-4 py-3 rounded-xl shadow-2xl shadow-indigo-950/60 animate-in slide-in-from-bottom-3 duration-200 font-sans text-xs">
          <CheckCircle2 className="w-4 h-4 text-emerald-400 shrink-0" />
          <span className="font-medium">{toastMessage}</span>
        </div>
      )}
    </div>
  );
};

export default App;
