import api from "./api";

export interface LoginParams {
  username: string;
  password: string;
}

export interface RegisterParams {
  username: string;
  password: string;
  email?: string;
}

export interface TokenResponse {
  access_token: string;
  refresh_token: string;
  token_type: string;
  user_id: number;
  username: string;
}

export const authService = {
  async login(params: LoginParams): Promise<TokenResponse> {
    const { data } = await api.post("/auth/login", params);
    return data;
  },

  async register(params: RegisterParams): Promise<{ user_id: number; username: string }> {
    const { data } = await api.post("/auth/register", params);
    return data;
  },

  logout() {
    localStorage.removeItem("access_token");
    localStorage.removeItem("refresh_token");
  },
};
