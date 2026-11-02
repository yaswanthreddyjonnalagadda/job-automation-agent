export type ApplicationStatus = 'submitted' | 'skipped' | 'needs_attention' | 'in_progress';

export interface Application {
  id: number;
  company: string;
  role: string;
  location: string;
  source: string;
  url: string;
  status: ApplicationStatus;
  statusDetail: string;
  timestamp: string;
  resumeUsed?: string;
  notes?: string;
}

export interface SuggestedJob {
  id: string;
  company: string;
  role: string;
  url: string;
  matchPercentage: number;
  source: 'Greenhouse' | 'Lever' | 'Workday' | 'Ashby' | string;
  tags: string[];
  location: string;
}

export interface ActiveMonitorTask {
  title: string;
  company: string;
  url: string;
  status: 'running' | 'idle' | 'paused';
  startedAt: string;
  currentStep: string;
  currentStepIndex: number;
  totalSteps: number;
  logLines: string[];
}

export interface ApplicationStats {
  totalTracked: number;
  submitted: number;
  needsYou: number;
  inProgress: number;
  skipped: number;
}

