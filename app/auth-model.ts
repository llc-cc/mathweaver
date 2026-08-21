export interface AuthenticatedUser {
  id: number;
  student_no: string | null;
  email: string | null;
  display_name: string;
  role: "student" | "teacher" | "admin";
  initial_password_pending: boolean;
}

export interface AuthState {
  token: string;
  user: AuthenticatedUser;
}

export interface AuthModalProps {
  onAuth: (auth: AuthState) => void;
  onSkip: () => void;
}

