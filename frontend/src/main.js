import { createApp } from 'vue';
import { takeToken } from './api.js';
import App from './App.vue';
import './style.css';

const initial = takeToken();
createApp(App, { initial }).mount('#app');
