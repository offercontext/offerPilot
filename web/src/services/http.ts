import axios, { type AxiosRequestConfig } from 'axios';
import { authHeaders } from './authToken';

export function createApiClient(config: AxiosRequestConfig = {}) {
  const client = axios.create(config);
  client.interceptors.request.use((requestConfig) => {
    requestConfig.headers = requestConfig.headers ?? {};
    Object.assign(requestConfig.headers, authHeaders());
    return requestConfig;
  });
  return client;
}
