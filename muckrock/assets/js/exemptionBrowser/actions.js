/* actions.js
**
** Exports actions for use by the exemption browser.
*/

import axios from 'axios';

export const UPDATE_EXEMPTION_QUERY = 'UPDATE_EXEMPTION_QUERY';
export const UPDATE_EXEMPTION_RESULTS = 'UPDATE_EXEMPTION_RESULTS';
export const LOAD_EXEMPTION_RESULTS = 'LOAD_EXEMPTION_RESULTS';
export const UPDATE_VISIBILITY_FILTER = 'UPDATE_VISIBILITY_FILTER';
export const SELECT_EXEMPTION = 'SELECT_EXEMPTION';
export const RESET_EXEMPTION_STATE = 'RESET_EXEMPTION_STATE';

export const updateExemptionQuery = (query) => (
    {
        type: UPDATE_EXEMPTION_QUERY,
        query: query,
    }
);

export const updateExemptionResults = (results) => (
    {
        type: UPDATE_EXEMPTION_RESULTS,
        results: results,
    }
);

export const loadExemptionResults = () => (
    {
        type: LOAD_EXEMPTION_RESULTS,
    }
);

export const updateVisibilityFilter = (filter) => (
    {
        type: UPDATE_VISIBILITY_FILTER,
        filter: filter,
    }
);

export const selectExemption = (exemption) => (
    {
        type: SELECT_EXEMPTION,
        exemption: exemption,
    }
);

export const resetExemptionState = () => (
    {
        type: RESET_EXEMPTION_STATE,
    }
);

export const searchExemptions = (searchQuery) => {
    const endpoint = '/api_v1/exemption/search/';
    return (dispatch) => {
        dispatch(loadExemptionResults());
        dispatch(updateExemptionQuery(searchQuery.q));
        return axios.get(endpoint, {
            params: searchQuery
        }).then(response => {
            const results = response.data.results;
            dispatch(updateExemptionResults(results));
        }).catch(error => {
            // TODO Handle errors by dispatching another action
            console.error(error);
        })
    }
};

export const submitExemption = (exemptionData) => {
    const endpoint = 'exemptions/submit';
    return axios.post(endpoint, {
        data: exemptionData,
        xsrfCookieName: 'csrftoken',
        xsrfHeaderName: 'X-CSRFToken',
    }).then(response => {
        // TODO Hanel successful submission
        console.debug('Posted successfully:', response);
    }).catch(error => {
        // TODO Handle submission error
        console.debug('Posted unsuccessfully:', error);
    })
};
