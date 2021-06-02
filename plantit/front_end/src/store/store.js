import Vue from 'vue';
import Vuex from 'vuex';
import createPersistedState from 'vuex-persistedstate';
import axios from 'axios';
import Cookies from 'js-cookie';
import { user } from '@/store/user';
import { users } from '@/store/users';
import { alerts } from '@/store/alerts';
import { agents } from '@/store/agents';
import { workflows } from '@/store/workflows';
import { submissions } from '@/store/submissions';
import { datasets } from '@/store/datasets';
import { notifications } from '@/store/notifications';

Vue.use(Vuex);

const store = new Vuex.Store({
    plugins: [
        createPersistedState({
            storage: window.sessionStorage
        })
    ],
    state: () => ({
        csrfToken: Cookies.get(axios.defaults.xsrfCookieName)
    }),
    getters: {
        csrfToken: state => state.csrfToken
    },
    modules: {
        user,
        users,
        alerts,
        agents,
        datasets,
        workflows,
        submissions,
        notifications
    }
});

export default store;
