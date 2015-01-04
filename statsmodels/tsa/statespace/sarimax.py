"""
ARMA Model

Author: Chad Fulton
License: Simplified-BSD
"""
from __future__ import division, absolute_import, print_function

from warnings import warn

import numpy as np
from .model import Model, StatespaceResults
from .tools import (
    companion_matrix, diff, is_invertible, constrain_stationary_univariate,
    unconstrain_stationary_univariate
)
from scipy.linalg import solve_discrete_lyapunov
from statsmodels.tsa.tsatools import lagmat
from statsmodels.tools.decorators import cache_readonly


class SARIMAX(Model):
    """
    Seasonal AutoRegressive Integrated Moving Average with eXogenous regressors
    model

    Parameters
    ----------
    endog : array_like
        The observed time-series process :math:`y`
    exog : array_like, optional
        Exogenous regressors, shaped nobs x k.
    order : iterable or iterable of iterables, optional
        The (p,d,q) order of the model for the number of AR parameters,
        differences, and MA parameters. `d` must be an integer
        indicating the integration order of the process, while
        `p` and `q` may either be an integers indicating the AR and MA
        orders (so that all lags up to those orders are included) or else
        iterables giving specific AR and / or MA lags to include. Default is
        an AR(1) model: (1,0,0).
    seasonal_order : iterable, optional
        The (P,D,Q,s) order of the seasonal component of the model for the
        AR parameters, differences, MA parameters, and number of seasons.
        `d` must be an integer indicating the integration order of the process,
        while `p` and `q` may either be an integers indicating the AR and MA
        orders (so that all lags up to those orders are included) or else
        iterables giving specific AR and / or MA lags to include. `s` is an
        integer giving the number of seasons, often it is 4 for quarterly data
        or 12 for monthly data. Default is no seasonal effect.
    trend : str{'n','c','t','ct'} or iterable, optional
        Parameter controlling the deterministic trend polynomial :math:`A(t)`.
        Can be specified as a string where 'c' indicates a constant (i.e. a
        degree zero component of the trend polynomial), 't' indicates a
        linear trend with time, and 'ct' is both. Can also be specified as an
        iterable defining the polynomial as in `numpy.poly1d`, where
        `[1,1,0,1]` would denote :math:`a + bt + ct^3`. Default is to not
        include a trend component.
    measurement_error : boolean, optional
        Whether or not to assume the observations were measured with error.
        Default is False.
    time_varying_regression : boolean, optional
        Used when an exogenous dataset is provided to select whether or not
        coefficients on the exogenous regressors are allowed to vary over time.
        Default is False.
    mle_regression : boolean, optional
        Whether or not to use estimate the regression coefficients as part of
        maximum likelihood estimation or through the Kalman filter (i.e.
        recursive least squares). If `time_varying_regression` is True, this
        must be set to False. Default is True.
    simple_differencing : boolean, optional
        Whether or not to use conditional maximum likelihood estimation.
        If True, differencing is performed prior to estimation, which discards
        the first :math:`SD + d` initial rows but reuslts in a smaller
        state-space formulation. If False, the full SARIMAX model is put in
        state-space form so that all datapoints can be used in estimation.
        Default is False.
    enforce_stationarity : boolean, optional
        Whether or not to transform the AR parameters to enforce stationarity
        in the autoregressive component of the model. Default is True.
    enforce_invertibility : boolean, optional
        Whether or not to transform the MA parameters to enforce invertibility
        in the moving average component of the model. Default is True.
    hamilton_representation : boolean, optional
        Whether or not to use the Hamilton representation of an ARMA process
        as a time series (if True) or the Harvey representation (if False).
        Default is False.

    Notes
    -----

    The SARIMA model is specified :math:`(p, d, q) \times (P, D, Q)_s`.

    .. math::

        \phi_p (L) \tilde \phi_P (L^s) \Delta^d \Delta_s^D y_t = A(t) +
            \theta_q (L) \tilde \theta_Q (L^s) \zeta_t

    In terms of a univariate structural model, this can be represented as

    .. math::

        y_t = u_t + \eta_t \\
        \phi_p (L) \tilde \phi_P (L^s) \Delta^d \Delta_s^D u_t = A(t) +
            \theta_q (L) \tilde \theta_Q (L^s) \zeta_t

    where :math:`\eta_t` is only applicable in the case of measurement error
    (although it is also used in the case of a pure regression model, i.e. if
    p=q=0).

    In terms of this model, regression with SARIMA errors can be represented
    easily as

    .. math::

        y_t = \beta_t x_t + u_t \\
        \phi_p (L) \tilde \phi_P (L^s) \Delta^d \Delta_s^D u_t = A(t) +
            \theta_q (L) \tilde \theta_Q (L^s) \zeta_t

    this model is the one used when exogenous regressors are provided.

    Note that the reduced form lag polynomials will be written as:

    .. math::

        \Phi (L) \equiv \phi_p (L) \tilde \phi_P (L^s) \\
        \Theta (L) \equiv \theta_q (L) \tilde \theta_Q (L^s)

    If `mle_regression` is True, regression coefficients are treated as
    additional parameters to be estimated via maximum likelihood. Otherwise
    they are included as part of the state with a diffuse initialization.
    In this case, however, with approximate diffuse initialization, results
    can be sensitive to the initial variance.

    This class allows two different underlying representations of ARMA models
    as state space models: that of Hamilton and that of Harvey. Both are
    equivalent in the sense that they are analytical representations of the
    ARMA model, but the state vectors of each have different meanings. For
    this reason, maximum likelihood does not result in identical parameter
    estimates and even the same set of parameters will result in different
    loglikelihoods.

    The Harvey representation is convenient because it allows integrating
    differencing into the state vector to allow using all observations for
    estimation.

    In this implementation of differenced models, the Hamilton representation
    is not able to accomodate differencing in the state vector, so
    `simple_differencing` (which performs differencing prior to estimation so
    that the first d + sD observations are lost) must be used.

    Many other packages use the Hamilton representation, so that tests against
    Stata and R require using it along with simple differencing (as Stata
    does).

    References
    ----------

    .. [1] Durbin, James, and Siem Jan Koopman. 2012.
       Time Series Analysis by State Space Methods: Second Edition.
       Oxford University Press.
    """

    def __init__(self, endog, exog=None, order=(1, 0, 0),
                 seasonal_order=(0, 0, 0, 0), trend=None,
                 measurement_error=False, time_varying_regression=False,
                 mle_regression=True, simple_differencing=False,
                 enforce_stationarity=True, enforce_invertibility=True,
                 hamilton_representation=False, *args, **kwargs):

        # Model parameters
        self.k_seasons = seasonal_order[3]
        self.measurement_error = measurement_error
        self.time_varying_regression = time_varying_regression
        self.mle_regression = mle_regression
        self.simple_differencing = simple_differencing
        self.enforce_stationarity = enforce_stationarity
        self.enforce_invertibility = enforce_invertibility
        self.hamilton_representation = hamilton_representation

        # Enforce non-MLE coefficients if time varying coefficients is
        # specified
        if self.time_varying_regression and self.mle_regression:
            raise ValueError('Models with time-varying regression coefficients'
                             ' must integrate the coefficients as part of the'
                             ' state vector, so that `mle_regression` must'
                             ' be set to False.')

        if self.time_varying_regression:
            # TODO should work, just needs unit tests
            raise NotImplementedError

        # Lag polynomials
        # Assume that they are given from lowest degree to highest, that all
        # degrees except for the constant are included, and that they are
        # boolean vectors (0 for not included, 1 for included).
        if isinstance(order[0], int):
            self.polynomial_ar = np.r_[1., np.ones(order[0])]
        else:
            self.polynomial_ar = np.r_[1., order[0]]
        if isinstance(order[2], int):
            self.polynomial_ma = np.r_[1., np.ones(order[2])]
        else:
            self.polynomial_ma = np.r_[1., order[2]]
        # Assume that they are given from lowest degree to highest, that the
        # degrees correspond to (1*s, 2*s, ..., P*s), and that they are
        # boolean vectors (0 for not included, 1 for included).
        if isinstance(seasonal_order[0], int):
            self.polynomial_seasonal_ar = np.r_[
                1.,  # constant
                ([0] * (self.k_seasons-1) + [1]) * seasonal_order[0]
            ]
        else:
            self.polynomial_seasonal_ar = np.r_[
                1., [0]*self.k_seasons*len(seasonal_order[0])
            ]
            for i in range(len(seasonal_order[0])):
                self.polynomial_seasonal_ar[(i+1)*self.k_seasons] = (
                    seasonal_order[0][i]
                )
        if isinstance(seasonal_order[2], int):
            self.polynomial_seasonal_ma = np.r_[
                1.,  # constant
                ([0] * (self.k_seasons-1) + [1]) * seasonal_order[2]
            ]
        else:
            self.polynomial_seasonal_ma = np.r_[
                1., [0]*self.k_seasons*len(seasonal_order[2])
            ]
            for i in range(len(seasonal_order[2])):
                self.polynomial_seasonal_ma[(i+1)*self.k_seasons] = (
                    seasonal_order[2][i]
                )

        # Deterministic trend polynomial
        self.trend = trend
        if trend is None or trend == 'n':
            self.polynomial_trend = np.ones((0))
        elif trend == 'c':
            self.polynomial_trend = np.r_[1]
        elif trend == 't':
            self.polynomial_trend = np.r_[0, 1]
        elif trend == 'ct':
            self.polynomial_trend = np.r_[1, 1]
        else:
            self.polynomial_trend = (np.array(trend) > 0).astype(int)

        # Model orders
        # Note: k_ar, k_ma, k_seasonal_ar, k_seasonal_ma do not include the
        # constant term, so they may be zero.
        # Note: for a typical ARMA(p,q) model, p = k_ar_params = k_ar - 1 and
        # q = k_ma_params = k_ma - 1, although this may not be true for models
        # with arbitrary log polynomials.
        self.k_ar = int(self.polynomial_ar.shape[0] - 1)
        self.k_ar_params = int(np.sum(self.polynomial_ar) - 1)
        self.k_diff = int(order[1])
        self.k_ma = int(self.polynomial_ma.shape[0] - 1)
        self.k_ma_params = int(np.sum(self.polynomial_ma) - 1)

        self.k_seasonal_ar = int(self.polynomial_seasonal_ar.shape[0] - 1)
        self.k_seasonal_ar_params = int(np.sum(self.polynomial_seasonal_ar) - 1)
        self.k_seasonal_diff = int(seasonal_order[1])
        self.k_seasonal_ma = int(self.polynomial_seasonal_ma.shape[0] - 1)
        self.k_seasonal_ma_params = int(np.sum(self.polynomial_seasonal_ma) - 1)

        # Make internal copies of the differencing orders because if we use
        # simple differencing, then we will need to internally use zeros after
        # the simple differencing has been performed
        self._k_diff = self.k_diff
        self._k_seasonal_diff = self.k_seasonal_diff

        # We can only use the Hamilton representation if differencing is not
        # performed as a part of the state space
        if (self.hamilton_representation and not (self.simple_differencing or
           self._k_diff == self._k_seasonal_diff == 0)):
            raise ValueError('The Hamilton representation is only available'
                             ' for models in which there is no differencing'
                             ' integrated into the state vector. Set'
                             ' `simple_differencing` to True or set'
                             ' `hamilton_representation` to False')

        # Note: k_trend is not the degree of the trend polynomial, because e.g.
        # k_trend = 1 corresponds to the degree zero polynomial (with only a
        # constant term).
        self.k_trend = int(np.sum(self.polynomial_trend))

        # Model order
        # (this is used internally in a number of locations)
        self._k_order = max(self.k_ar + self.k_seasonal_ar,
                            self.k_ma + self.k_seasonal_ma + 1)
        if self.k_ar == self.k_ma == 0:
            self._k_order = 0

        # Exogenous data
        self.k_exog = 0
        if exog is not None:
            exog = np.asarray(exog)

            # Make sure we have 2-dimensional array
            if exog.ndim == 1:
                exog = exog[:, None]

            self.k_exog = exog.shape[1]
        # Redefine mle_regression to be true only if it was previously set to
        # true and there are exogenous regressors
        self.mle_regression = (
            self.mle_regression and exog is not None and self.k_exog > 0
        )
        # State regression is regression with coefficients estiamted within
        # the state vector
        self.state_regression = (
            not self.mle_regression and exog is not None and self.k_exog > 0
        )
        # If all we have is a regression (so k_ar = k_ma = 0), then put the
        # error term as measurement error
        if self.state_regression and self._k_order == 0:
            self.measurement_error = True

        # Number of states
        k_states = self._k_order
        if not self.simple_differencing:
            k_states += self.k_seasons * self._k_seasonal_diff + self._k_diff
        if self.state_regression:
            k_states += self.k_exog

        # Number of diffuse states
        k_diffuse_states = k_states
        if self.enforce_stationarity:
            k_diffuse_states -= self._k_order

        # Number of positive definite elements of the state covariance matrix
        k_posdef = int(self._k_order > 0)
        # Only have an error component to the states if k_posdef > 0
        self.state_error = k_posdef > 0
        if self.state_regression and self.time_varying_regression:
            k_posdef += self.k_exog

        # Diffuse initialization can be more sensistive to the variance value
        # in the case of state regression, so set a higher than usual default
        # variance
        if self.state_regression:
            kwargs.setdefault('initial_variance', 1e10)

        # Number of parameters
        self.k_params = (
            self.k_ar_params + self.k_ma_params +
            self.k_seasonal_ar_params + self.k_seasonal_ar_params +
            self.k_trend +
            self.measurement_error + 1
        )
        if self.mle_regression:
            self.k_params += self.k_exog

        # Perform simple differencing if requested
        self.orig_endog = endog
        self.orig_exog = exog
        if (simple_differencing and
           (self._k_diff > 0 or self._k_seasonal_diff > 0)):
            # Save the originals
            self.orig_endog = np.copy(endog)
            self.orig_exog = np.copy(exog)
            # Perform simple differencing
            endog = diff(endog, self._k_diff, self._k_seasonal_diff,
                         self.k_seasons)
            if exog is not None:
                exog = diff(exog, self._k_diff, self._k_seasonal_diff,
                            self.k_seasons)
            self._k_diff = 0
            self._k_seasonal_diff = 0

        # Set some model variables now so they will be available for the
        # initialize() method, below
        self.nobs = len(endog)
        self.k_states = k_states
        self.k_posdef = k_posdef

        # By default, do not calculate likelihood while it is controlled by
        # diffuse initial conditions.
        kwargs.setdefault('loglikelihood_burn', k_diffuse_states)

        # Set the default results class to be SARIMAXResults
        kwargs.setdefault('filter_results_class', SARIMAXResults)

        # Initialize the statespace
        super(SARIMAX, self).__init__(
            endog, exog=exog, k_states=k_states, k_posdef=k_posdef,
            *args, **kwargs
        )

        # Initialize the fixed components of the statespace model
        self.design = self.initial_design
        self.state_intercept = self.initial_state_intercept
        self.transition = self.initial_transition
        self.selection = self.initial_selection

        # If we are estimating a simple ARMA model, then we can use a faster
        # initialization method.
        if k_diffuse_states == 0:
            self.initialize_stationary()

    def initialize(self):
        # Internal flag for whether the default mixed approximate diffuse /
        # stationary initialization has been overridden with a user-supplied
        # initialization
        self._manual_initialization = False

        # Cache the indexes of included polynomial orders (for update below)
        # (but we do not want the index of the constant term, so exclude the
        # first index)
        self._polynomial_ar_idx = np.nonzero(self.polynomial_ar)[0][1:]
        self._polynomial_ma_idx = np.nonzero(self.polynomial_ma)[0][1:]
        self._polynomial_seasonal_ar_idx = np.nonzero(
            self.polynomial_seasonal_ar
        )[0][1:]
        self._polynomial_seasonal_ma_idx = np.nonzero(
            self.polynomial_seasonal_ma
        )[0][1:]

        # Save the indices corresponding to the reduced form lag polynomial
        # parameters in the transition and selection matrices so that they
        # don't have to be recalculated for each update()
        start_row = self._k_diff + self.k_seasons*self._k_seasonal_diff
        end_row = start_row + self.k_ar + self.k_seasonal_ar
        col = self._k_diff + self.k_seasons*self._k_seasonal_diff
        if not self.hamilton_representation:
            self.transition_ar_params_idx = np.s_[start_row:end_row, col]
        else:
            self.transition_ar_params_idx = np.s_[col, start_row:end_row]

        start_row += 1
        end_row = start_row + self.k_ma + self.k_seasonal_ma
        col = 0
        if not self.hamilton_representation:
            self.selection_ma_params_idx = np.s_[start_row:end_row, col]
        else:
            self.design_ma_params_idx = np.s_[col, start_row:end_row]

        # Cache the arrays for calculating the intercept from the trend
        # components
        self._trend_data = np.ones((self.nobs, self.k_trend))
        if self.k_trend > 1:
            self._trend_data[:, 1] = np.arange(1, self.nobs+1)
        if self.k_trend > 2:
            for i in range(2, self.k_trend):
                self._trend_data[:, i] = (
                    self._trend_data[:, i-1] * self._trend_data[:, 1]
                )

        # Cache indices for exog variances in the state covariance matrix
        if self.state_regression and self.time_varying_regression:
            idx = np.diag_indices(self.k_posdef)
            self._exog_variance_idx = (idx[0][-self.k_exog:],
                                       idx[1][-self.k_exog:])

    def initialize_known(self, initial_state, initial_state_cov):
        self._manual_initialization = True
        super(SARIMAX, self).initialize_known(initial_state, initial_state_cov)

    def initialize_approximate_diffuse(self, variance=None):
        self._manual_initialization = True
        super(SARIMAX, self).initialize_approximate_diffuse(variance)

    def initialize_stationary(self):
        self._manual_initialization = True
        super(SARIMAX, self).initialize_stationary()

    def initialize_state(self, variance=None):
        # Check if a manual initialization has already been specified
        if self._manual_initialization:
            return

        # If we're not enforcing stationarity, then we can't initialize a
        # stationary component
        if not self.enforce_stationarity:
            self.initialize_approximate_diffuse(variance)

        # Otherwise, create the initial state and state covariance matrix
        # as from a combination of diffuse and stationary components

        # Create initialized non-stationary components
        if variance is None:
            variance = self.initial_variance

        initial_state = np.zeros(self.k_states, dtype=self.transition.dtype)
        initial_state_cov = (
            np.eye(self.k_states, dtype=self.transition.dtype) * variance
        )

        # Get the offsets (from the bottom or bottom right of the vector /
        # matrix) for the stationary component.
        if self.state_regression:
            start = -(self.k_exog + self._k_order)
            end = -self.k_exog if self.k_exog > 0 else None
        else:
            start = -self._k_order
            end = None

        # Add in the initialized stationary components
        if self._k_order > 0:
            selection_stationary = self.selection[start:end, :, 0]
            selected_state_cov_stationary = np.dot(
                np.dot(selection_stationary, self.state_cov[:, :, 0]),
                selection_stationary.T
            )
            initial_state_cov_stationary = solve_discrete_lyapunov(
                self.transition[start:end, start:end, 0],
                selected_state_cov_stationary
            )

            initial_state_cov[start:end, start:end] = initial_state_cov_stationary

        super(SARIMAX, self).initialize_known(initial_state, initial_state_cov)

    @property
    def initial_design(self):
        # Basic design matrix
        design = np.r_[
            [1] * self._k_diff,
            ([0] * (self.k_seasons-1) + [1]) * self._k_seasonal_diff,
            [1] * self.state_error, [0] * (self._k_order-1)
        ]

        # If we have exogenous regressors included as part of the state vector
        # then the exogenous data is incorporated as a time-varying component
        # of the design matrix
        if self.state_regression:
            if self._k_order > 0:
                design = np.c_[
                    np.reshape(
                        np.repeat(design, self.nobs),
                        (design.shape[0], self.nobs)
                    ).T,
                    self.exog
                ].T[None, :, :]
            else:
                design = self.exog.T[None, :, :]
        return design

    @property
    def initial_state_intercept(self):
        # TODO make this self.k_trend > 1 and adjust the update to take
        # into account that if the trend is a constant, it is not time-varying
        if self.k_trend > 0:
            state_intercept = np.zeros((self.k_states, self.nobs))
        else:
            state_intercept = np.zeros((self.k_states,))
        return state_intercept

    @property
    def initial_transition(self):
        transition = np.zeros((self.k_states, self.k_states))

        # Exogenous regressors component
        if self.state_regression:
            start = -self.k_exog
            # T_\beta
            transition[start:, start:] = np.eye(self.k_exog)

            # Autoregressive component
            start = -(self.k_exog + self._k_order)
            end = -self.k_exog if self.k_exog > 0 else None
        else:
            # Autoregressive component
            start = -self._k_order
            end = None

        # T_c
        transition[start:end, start:end] = companion_matrix(self._k_order)
        if self.hamilton_representation:
            transition[start:end, start:end] = np.transpose(
                companion_matrix(self._k_order)
            )

        # Seasonal differencing component
        # T^*
        if self._k_seasonal_diff > 0:
            seasonal_companion = companion_matrix(self.k_seasons).T
            seasonal_companion[0, -1] = 1
            for d in range(self._k_seasonal_diff):
                start = self._k_diff + d * self.k_seasons
                end = self._k_diff + (d+1) * self.k_seasons

                # T_c^*
                transition[start:end, start:end] = seasonal_companion

                # i
                for i in range(d+1, self._k_seasonal_diff):
                    transition[start, end + self.k_seasons - 1] = 1

                # \iota
                transition[start, self._k_diff + self.k_seasons*self._k_seasonal_diff] = 1

        # Differencing component
        if self._k_diff > 0:
            idx = np.triu_indices(self._k_diff)
            # T^**
            transition[idx] = 1
            # [0 1]
            if self.k_seasons > 0:
                start = self._k_diff
                end = self._k_diff + self.k_seasons*self._k_seasonal_diff
                transition[:self._k_diff, start:end] = ([0] * (self.k_seasons-1) + [1]) * self._k_seasonal_diff
            # [1 0]
            column = self._k_diff + self.k_seasons*self._k_seasonal_diff
            transition[:self._k_diff, column] = 1

        return transition

    @property
    def initial_selection(self):
        if not (self.state_regression and self.time_varying_regression):
            if self.k_posdef > 0:
                selection = np.r_[
                    [0] * (self._k_diff + self.k_seasons*self._k_seasonal_diff),
                    [1] * (self._k_order > 0), [0] * (self._k_order-1),
                    [0] * ((1 - self.mle_regression) * self.k_exog)
                ][:, None]
            else:
                selection = np.zeros((self.k_states, 0))
        else:
            selection = np.zeros((self.k_states, self.k_posdef))
            # Typical state variance
            if self._k_order > 0:
                selection[0, 0] = 1
            # Time-varying regression coefficient variances
            for i in range(self.k_exog, 0, -1):
                selection[-i, -i] = 1
        return selection

    @staticmethod
    def _conditional_sum_squares(endog, k_ar, polynomial_ar, k_ma,
                                 polynomial_ma, k_trend=0, trend_data=None):
        k = 2*k_ma
        r = max(k+k_ma, k_ar)

        residuals = None
        if k_ar + k_ma + k_trend > 0:
            # If we have MA terms, get residuals from an AR(k) model to use
            # as data for conditional sum of squares estimates of the MA
            # parameters
            if k_ma > 0:
                Y = endog[k:]
                X = lagmat(endog, k, trim='both')
                params_ar = np.linalg.pinv(X).dot(Y)
                residuals = Y - np.dot(X, params_ar)

            # Run an ARMA(p,q) model using the just computed residuals as data
            Y = endog[r:]

            X = np.empty((Y.shape[0], 0))
            if k_trend > 0:
                if trend_data is None:
                    raise ValueError('Trend data must be provided if'
                                     ' `k_trend` > 0.')
                X = np.c_[X, trend_data[:-r, :]]
            if k_ar > 0:
                X = np.c_[X, lagmat(endog, k_ar)[r:, polynomial_ar.nonzero()[0][1:]-1]]
            if k_ma > 0:
                X = np.c_[X, lagmat(residuals, k_ma)[r-k:, polynomial_ma.nonzero()[0][1:]-1]]

            # Get the array of [ar_params, ma_params]
            params = np.linalg.pinv(X).dot(Y)
            residuals = Y - np.dot(X, params)

        # Default output
        params_trend = []
        params_ar = []
        params_ma = []
        params_variance = []

        # Get the params
        offset = 0
        if k_trend > 0:
            params_trend = params[offset]
            offset += k_trend
        if k_ar > 0:
            params_ar = params[offset:k_ar+offset]
            offset += k_ar
        if k_ma > 0:
            params_ma = params[offset:k_ma+offset]
            offset += k_ma
        if residuals is not None:
            params_variance = (residuals[k_ma:]**2).mean()

        return (params_trend, params_ar, params_ma,
                params_variance)

    @property
    def start_params(self):
        """
        Starting parameters for maximum likelihood estimation
        """

        # Perform differencing if necessary (i.e. if simple differencing is
        # false so that the state-space model will use the entire dataset)
        trend_data = self._trend_data
        if not self.simple_differencing and (
           self._k_diff > 0 or self._k_seasonal_diff > 0):
            endog = diff(self.endog[0, :], self._k_diff,
                         self._k_seasonal_diff, self.k_seasons)
            if self.exog is not None:
                exog = diff(self.exog, self._k_diff,
                            self._k_seasonal_diff, self.k_seasons)
            trend_data = trend_data[:endog.shape[0], :]
        else:
            endog = self.endog.copy()[0, :]
            exog = self.exog.copy() if self.exog is not None else None

        # Regression effects via OLS
        params_exog = []
        if self.k_exog > 0:
            params_exog = np.linalg.pinv(exog).dot(endog)
            endog -= np.dot(exog, params_exog)
        if self.state_regression:
            params_exog = []

        # Although the Kalman filter can deal with missing values in endog,
        # conditional sum of squares cannot
        endog = endog[~np.isnan(endog)]

        # Non-seasonal ARMA component and trend
        (params_trend, params_ar, params_ma,
         params_variance) = self._conditional_sum_squares(
            endog, self.k_ar, self.polynomial_ar, self.k_ma,
            self.polynomial_ma, self.k_trend, trend_data
        )

        # If we have estimated non-stationary start parameters but enforce
        # stationarity is on, raise an error
        if self.k_ar > 0 and self.enforce_stationarity and not is_invertible(-params_ar):
            raise ValueError('Non-stationary starting autoregressive'
                             ' parameters found with `enforce_stationarity`'
                             ' set to True.')

        # If we have estimated non-invertible start parameters but enforce
        # invertibility is on, raise an error
        if self.k_ma > 0 and self.enforce_invertibility and not is_invertible(params_ma):
            raise ValueError('non-invertible starting MA parameters found'
                             ' with `enforce_invertibility` set to True.')

        # Seasonal Parameters
        _, params_seasonal_ar, params_seasonal_ma, params_seasonal_variance = (
            self._conditional_sum_squares(
                endog, self.k_seasonal_ar, self.polynomial_seasonal_ar,
                self.k_seasonal_ma, self.polynomial_seasonal_ma
            )
        )

        # If we have estimated non-stationary start parameters but enforce
        # stationarity is on, raise an error
        if self.k_seasonal_ar > 0 and self.enforce_stationarity and not is_invertible(-params_seasonal_ar):
            raise ValueError('Non-stationary starting autoregressive'
                             ' parameters found with `enforce_stationarity`'
                             ' set to True.')

        # If we have estimated non-invertible start parameters but enforce
        # invertibility is on, raise an error
        if self.k_seasonal_ma > 0 and self.enforce_invertibility and not is_invertible(params_seasonal_ma):
            raise ValueError('non-invertible starting seasonal moving average'
                             ' parameters found with `enforce_invertibility`'
                             ' set to True.')

        # Variances
        params_exog_variance = []
        if self.state_regression and self.time_varying_regression:
            # TODO how to set the initial variance parameters?
            params_exog_variance = [1] * self.k_exog
        if self.state_error and params_variance == []:
            if not params_seasonal_variance == []:
                params_variance = params_seasonal_variance
            elif self.k_exog > 0:
                params_variance = np.dot(endog, endog)
            else:
                params_variance = 1
        params_measurement_variance = 1 if self.measurement_error else []

        # Combine all parameters
        return np.r_[
            params_trend,
            params_exog,
            params_ar,
            params_ma,
            params_seasonal_ar,
            params_seasonal_ma,
            params_exog_variance,
            params_measurement_variance,
            params_variance
        ]

    @property
    def endog_names(self, latex=False):
        diff = ''
        if self.k_diff > 0:
            if self.k_diff == 1:
                diff = '\Delta' if latex else 'D'
            else:
                diff = ('\Delta^%d' if latex else 'D%d') % self.k_diff

        seasonal_diff = ''
        if self.k_seasonal_diff > 0:
            if self.k_seasonal_diff == 1:
                seasonal_diff = (('\Delta_%d' if latex else 'DS%d') %
                                 (self.k_seasons))
            else:
                seasonal_diff = (('\Delta_%d^%d' if latex else 'D%dS%d') %
                                 (self.k_seasonal_diff, self.k_seasons))
        if self.k_diff > 0 and self.k_seasonal_diff > 0:
            return (('%s%s %s' if latex else '%s.%s.%s') %
                    (diff, seasonal_diff, self.data.ynames))
        elif self.k_diff > 0:
            return (('%s %s' if latex else '%s.%s') %
                    (diff, self.data.ynames))
        elif self.k_seasonal_diff > 0:
            return (('%s %s' if latex else '%s.%s') %
                    (seasonal_diff, self.data.ynames))
        else:
            return self.data.ynames

    params_complete = [
        'trend', 'exog', 'ar', 'ma', 'seasonal_ar', 'seasonal_ma',
        'exog_variance', 'measurement_variance', 'variance'
    ]

    @property
    def params_included(self):
        model_orders = self.model_orders
        # Get basic list from model orders
        params = [
            order for order in self.params_complete
            if model_orders[order] > 0
        ]
        # k_exog may be positive without associated parameters if it is in the
        # state vector
        if 'exog' in params and not self.mle_regression:
            params.remove('exog')

        return params

    @property
    def params_names(self):
        params_sort_order = self.params_included
        model_names = self.model_names
        return [
            name for param in params_sort_order for name in model_names[param]
        ]

    @property
    def model_orders(self):
        return {
            'trend': self.k_trend,
            'exog': self.k_exog,
            'ar': self.k_ar,
            'ma': self.k_ma,
            'seasonal_ar': self.k_seasonal_ar,
            'seasonal_ma': self.k_seasonal_ma,
            'reduced_ar': self.k_ar + self.k_seasonal_ar,
            'reduced_ma': self.k_ma + self.k_seasonal_ma,
            'exog_variance': self.k_exog if (
                self.state_regression and self.time_varying_regression) else 0,
            'measurement_variance': int(self.measurement_error),
            'variance': int(self.state_error),
        }

    @property
    def model_names(self):
        return self._get_model_names(latex=False)

    @property
    def model_latex_names(self):
        return self._get_model_names(latex=True)

    def _get_model_names(self, latex=False):
        names = {
            'trend': None,
            'exog': None,
            'ar': None,
            'ma': None,
            'seasonal_ar': None,
            'seasonal_ma': None,
            'reduced_ar': None,
            'reduced_ma': None,
            'exog_variance': None,
            'measurement_variance': None,
            'variance': None,
        }

        # Trend
        if self.k_trend > 0:
            trend_template = 't_%d' if latex else 'trend.%d'
            names['trend'] = []
            for i in self.polynomial_trend.nonzero()[0]:
                if i == 0:
                    names['trend'].append('intercept')
                elif i == 1:
                    names['trend'].append('drift')
                else:
                    names['trend'].append(trend_template % i)

        # Exogenous coefficients
        if self.k_exog > 0:
            names['exog'] = self.exog_names

        # Autoregressive
        if self.k_ar > 0:
            ar_template = '$\\phi_%d$' if latex else 'ar.L%d'
            names['ar'] = []
            for i in self.polynomial_ar.nonzero()[0][1:]:
                names['ar'].append(ar_template % i)

        # Moving Average
        if self.k_ma > 0:
            ma_template = '$\\theta_%d$' if latex else 'ma.L%d'
            names['ma'] = []
            for i in self.polynomial_ma.nonzero()[0][1:]:
                names['ma'].append(ma_template % i)

        # Seasonal Autoregressive
        if self.k_seasonal_ar > 0:
            seasonal_ar_template = '$\\tilde \\phi_%d$' if latex else 'ar.S.L%d'
            names['seasonal_ar'] = []
            for i in self.polynomial_seasonal_ar.nonzero()[0][1:]:
                names['seasonal_ar'].append(seasonal_ar_template % i)

        # Seasonal Moving Average
        if self.k_seasonal_ma > 0:
            seasonal_ma_template = '$\\tilde \\theta_%d$' if latex else 'ma.S.L%d'
            names['seasonal_ma'] = []
            for i in self.polynomial_seasonal_ma.nonzero()[0][1:]:
                names['seasonal_ma'].append(seasonal_ma_template % i)

        # Reduced Form Autoregressive
        if self.k_ar > 0 or self.k_seasonal_ar > 0:
            reduced_polynomial_ar = reduced_polynomial_ar = -np.polymul(
                self.polynomial_ar, self.polynomial_seasonal_ar
            )
            ar_template = '$\\Phi_%d$' if latex else 'ar.R.L%d'
            names['reduced_ar'] = []
            for i in reduced_polynomial_ar.nonzero()[0][1:]:
                names['reduced_ar'].append(ar_template % i)

        # Reduced Form Moving Average
        if self.k_ma > 0 or self.k_seasonal_ma > 0:
            reduced_polynomial_ma = np.polymul(
                self.polynomial_ma, self.polynomial_seasonal_ma
            )
            ma_template = '$\\Theta_%d$' if latex else 'ma.R.L%d'
            names['reduced_ma'] = []
            for i in reduced_polynomial_ma.nonzero()[0][1:]:
                names['reduced_ma'].append(ma_template % i)

        # Exogenous variances
        if self.state_regression and self.time_varying_regression:
            exog_var_template = '$\\sigma_\\text{%s}^2$' if latex else 'var.%s'
            names['exog_variance'] = [
                exog_var_template % exog_name for exog_name in self.exog_names
            ]

        # Measurement error variance
        if self.measurement_error:
            meas_var_tpl = (
                '$\\sigma_\\eta^2$' if latex else 'var.measurment_error'
            )
            names['measurement_variance'] = [meas_var_tpl]

        # State variance
        if self.state_error:
            var_tpl = '$\\sigma_\\zeta^2$' if latex else 'sigma2'
            names['variance'] = [var_tpl]

        return names

    def transform_params(self, unconstrained):
        """
        Transform unconstrained parameters used by the optimizer to constrained
        parameters used in likelihood evaluation

        TODO need to modify to work with lag polynomials containing missing
             lag orders.
        """
        constrained = np.zeros(unconstrained.shape, unconstrained.dtype)

        start = end = 0

        # Retain the trend parameters
        if self.k_trend > 0:
            end += self.k_trend
            constrained[start:end] = unconstrained[start:end]
            start += self.k_trend

        # Retain any MLE regression coefficients
        if self.mle_regression:
            end += self.k_exog
            constrained[start:end] = unconstrained[start:end]
            start += self.k_exog

        # Transform the AR parameters (phi) to be stationary
        if self.k_ar_params > 0:
            end += self.k_ar_params
            if self.enforce_stationarity:
                constrained[start:end] = constrain_stationary_univariate(unconstrained[start:end])
            else:
                constrained[start:end] = unconstrained[start:end]
            start += self.k_ar_params

        # Transform the MA parameters (theta) to be invertible
        if self.k_ma_params > 0:
            end += self.k_ma_params
            if self.enforce_invertibility:
                constrained[start:end] = constrain_stationary_univariate(unconstrained[start:end])
            else:
                constrained[start:end] = unconstrained[start:end]
            start += self.k_ma_params

        # Transform the seasonal AR parameters (\tilde phi) to be stationary
        if self.k_seasonal_ar > 0:
            end += self.k_seasonal_ar_params
            if self.enforce_stationarity:
                constrained[start:end] = constrain_stationary_univariate(unconstrained[start:end])
            else:
                constrained[start:end] = unconstrained[start:end]
            start += self.k_seasonal_ar_params

        # Transform the seasonal MA parameters (\tilde theta) to be invertible
        if self.k_seasonal_ma_params > 0:
            end += self.k_seasonal_ma_params
            if self.enforce_invertibility:
                constrained[start:end] = constrain_stationary_univariate(unconstrained[start:end])
            else:
                constrained[start:end] = unconstrained[start:end]
            start += self.k_seasonal_ma_params

        # Transform the standard deviation parameters to be positive
        if self.state_regression and self.time_varying_regression:
            end += self.k_exog
            constrained[start:end] = unconstrained[start:end]**2
            start += self.k_exog
        if self.measurement_error:
            constrained[start] = unconstrained[start]**2
            start += 1
            end += 1
        if self.state_error:
            constrained[start] = unconstrained[start]**2
            # start += 1
            # end += 1

        return constrained

    def untransform_params(self, constrained):
        """
        Transform constrained parameters used in likelihood evaluation
        to unconstrained parameters used by the optimizer
        """
        unconstrained = np.zeros(constrained.shape, constrained.dtype)

        start = end = 0

        # Retain the trend parameters
        if self.k_trend > 0:
            end += self.k_trend
            unconstrained[start:end] = constrained[start:end]
            start += self.k_trend

        # Retain any MLE regression coefficients
        if self.mle_regression:
            end += self.k_exog
            unconstrained[start:end] = constrained[start:end]
            start += self.k_exog

        # Transform the AR parameters (phi) to be stationary
        if self.k_ar_params > 0:
            end += self.k_ar_params
            if self.enforce_stationarity:
                unconstrained[start:end] = unconstrain_stationary_univariate(constrained[start:end])
            else:
                unconstrained[start:end] = constrained[start:end]
            start += self.k_ar_params

        # Transform the MA parameters (theta) to be invertible
        if self.k_ma_params > 0:
            end += self.k_ma_params
            if self.enforce_invertibility:
                unconstrained[start:end] = unconstrain_stationary_univariate(constrained[start:end])
            else:
                unconstrained[start:end] = constrained[start:end]
            start += self.k_ma_params

        # Transform the seasonal AR parameters (\tilde phi) to be stationary
        if self.k_seasonal_ar > 0:
            end += self.k_seasonal_ar_params
            if self.enforce_stationarity:
                unconstrained[start:end] = unconstrain_stationary_univariate(constrained[start:end])
            else:
                unconstrained[start:end] = constrained[start:end]
            start += self.k_seasonal_ar_params

        # Transform the seasonal MA parameters (\tilde theta) to be invertible
        if self.k_seasonal_ma_params > 0:
            end += self.k_seasonal_ma_params
            if self.enforce_invertibility:
                unconstrained[start:end] = unconstrain_stationary_univariate(constrained[start:end])
            else:
                unconstrained[start:end] = constrained[start:end]
            start += self.k_seasonal_ma_params

        # Untransform the standard deviation
        if self.state_regression and self.time_varying_regression:
            end += self.k_exog
            unconstrained[start:end] = constrained[start:end]**0.5
            start += self.k_exog
        if self.measurement_error:
            unconstrained[start] = constrained[start]**0.5
            start += 1
            end += 1
        if self.state_error:
            unconstrained[start] = constrained[start]**0.5
            # start += 1
            # end += 1

        return unconstrained

    def update(self, params, *args, **kwargs):
        params = super(SARIMAX, self).update(params, *args, **kwargs)

        params_trend = None
        params_exog = None
        params_ar = None
        params_ma = None
        params_seasonal_ar = None
        params_seasonal_ma = None
        params_exog_variance = None
        params_measurement_variance = None
        params_variance = None

        # Extract the parameters
        start = end = 0
        end += self.k_trend
        params_trend = params[start:end]
        start += self.k_trend
        if self.mle_regression:
            end += self.k_exog
            params_exog = params[start:end]
            start += self.k_exog
        end += self.k_ar_params
        params_ar = params[start:end]
        start += self.k_ar_params
        end += self.k_ma_params
        params_ma = params[start:end]
        start += self.k_ma_params
        end += self.k_seasonal_ar_params
        params_seasonal_ar = params[start:end]
        start += self.k_seasonal_ar_params
        end += self.k_seasonal_ma_params
        params_seasonal_ma = params[start:end]
        start += self.k_seasonal_ma_params
        if self.state_regression and self.time_varying_regression:
            end += self.k_exog
            params_exog_variance = params[start:end]
            start += self.k_exog
        if self.measurement_error:
            params_measurement_variance = params[start]
            start += 1
            end += 1
        if self.state_error:
            params_variance = params[start]
        # start += 1
        # end += 1

        # Update lag polynomials
        if self.k_ar > 0:
            if self.polynomial_ar.dtype == params.dtype:
                self.polynomial_ar[self._polynomial_ar_idx] = -params_ar
            else:
                polynomial_ar = self.polynomial_ar.real.astype(params.dtype)
                polynomial_ar[self._polynomial_ar_idx] = -params_ar
                self.polynomial_ar = polynomial_ar

        if self.k_ma > 0:
            if self.polynomial_ma.dtype == params.dtype:
                self.polynomial_ma[self._polynomial_ma_idx] = params_ma
            else:
                polynomial_ma = self.polynomial_ma.real.astype(params.dtype)
                polynomial_ma[self._polynomial_ma_idx] = params_ma
                self.polynomial_ma = polynomial_ma

        if self.k_seasonal_ar > 0:
            if self.polynomial_seasonal_ar.dtype == params.dtype:
                self.polynomial_seasonal_ar[self._polynomial_seasonal_ar_idx] = (
                    -params_seasonal_ar
                )
            else:
                polynomial_seasonal_ar = (
                    self.polynomial_seasonal_ar.real.astype(params.dtype)
                )
                polynomial_seasonal_ar[self._polynomial_seasonal_ar_idx] = (
                    -params_seasonal_ar
                )
                self.polynomial_seasonal_ar = polynomial_seasonal_ar

        if self.k_seasonal_ma > 0:
            if self.polynomial_seasonal_ma.dtype == params.dtype:
                self.polynomial_seasonal_ma[self._polynomial_seasonal_ma_idx] = (
                    params_seasonal_ma
                )
            else:
                polynomial_seasonal_ma = (
                    self.polynomial_seasonal_ma.real.astype(params.dtype)
                )
                polynomial_seasonal_ma[self._polynomial_seasonal_ma_idx] = (
                    params_seasonal_ma
                )
                self.polynomial_seasonal_ma = polynomial_seasonal_ma

        # Get the reduced form lag polynomial terms by multiplying the regular
        # and seasonal lag polynomials
        # Note: that although the numpy np.polymul examples assume that they
        # are ordered from highest degree to lowest, whereas our are from
        # lowest to highest, it does not matter.
        if self.k_seasonal_ar > 0:
            reduced_polynomial_ar = -np.polymul(
                self.polynomial_ar, self.polynomial_seasonal_ar
            )
        else:
            reduced_polynomial_ar = -self.polynomial_ar
        if self.k_seasonal_ma > 0:
            reduced_polynomial_ma = np.polymul(
                self.polynomial_ma, self.polynomial_seasonal_ma
            )
        else:
            reduced_polynomial_ma = self.polynomial_ma

        # Observation intercept
        # Exogenous data with MLE estimation of parameters enters through a
        # time-varying observation intercept (is equivalent to simply
        # subtracting it out of the endogenous variable first)
        if self.mle_regression:
            if self.obs_intercept.dtype == params.dtype:
                self.obs_intercept = np.dot(self.exog, params_exog)[None, :]
            else:
                obs_intercept = np.dot(
                    self.exog, params_exog
                )[None, :].astype(params.dtype)
                self.obs_intercept = obs_intercept

        # State intercept (Harvey) or additional observation intercept
        # (Hamilton)
        # SARIMA trend enters through the a time-varying state intercept,
        # associated with the first row of the stationary component of the
        # state vector (i.e. the first element of the state vector following
        # any differencing elements)
        if self.k_trend > 0:
            data = np.dot(self._trend_data, params_trend).astype(params.dtype)
            if not self.hamilton_representation:
                if self.state_intercept.dtype == params.dtype:
                    self.state_intercept[self._k_diff + self._k_seasonal_diff * self.k_seasons, :] = data
                else:
                    state_intercept = self.state_intercept.real.astype(params.dtype)
                    state_intercept[self._k_diff + self._k_seasonal_diff * self.k_seasons, :] = data
                    self.state_intercept = state_intercept
            else:
                # The way the trend enters in the Hamilton representation means
                # that the parameter is not an ``intercept'' but instead the
                # mean of the process. The trend values in `data` are meant for
                # an intercept, and so must be transformed to represent the
                # mean instead
                if self.hamilton_representation:
                    data /= np.sum(-reduced_polynomial_ar)

                # If we already set the observation intercept for MLE
                # regression, just add to it
                if self.mle_regression:
                    self.obs_intercept += data[None, :]
                # Otherwise set it directly
                else:
                    self.obs_intercept = data[None, :]

        # Observation covariance matrix
        if self.measurement_error:
            if self.obs_cov.dtype == params.dtype:
                self.obs_cov[0, 0] = params_measurement_variance
            else:
                obs_cov = self.obs_cov.real.astype(params.dtype)
                obs_cov[0, 0] = params_measurement_variance
                self.obs_cov = obs_cov

        # Transition matrix
        if self.k_ar > 0 or self.k_seasonal_ar > 0:
            if self.transition.dtype == params.dtype:
                self.transition[self.transition_ar_params_idx] = (
                    reduced_polynomial_ar[1:, None]
                )
            else:
                transition = self.transition.real.astype(params.dtype)
                transition[self.transition_ar_params_idx] = (
                    reduced_polynomial_ar[1:, None]
                )
                self.transition = transition
        elif not self.transition.dtype == params.dtype:
            self.transition = self.transition.real.astype(params.dtype)

        # Selection matrix (Harvey) or Design matrix (Hamilton)
        if self.k_ma > 0 or self.k_seasonal_ma > 0:
            if not self.hamilton_representation:
                if self.selection.dtype == params.dtype:
                    self.selection[self.selection_ma_params_idx] = (
                        reduced_polynomial_ma[1:, None]
                    )
                else:
                    selection = self.selection.real.astype(params.dtype)
                    selection[self.selection_ma_params_idx] = (
                        reduced_polynomial_ma[1:, None]
                    )
                    self.selection = selection
            else:
                if self.design.dtype == params.dtype:
                    self.design[self.design_ma_params_idx] = (
                        reduced_polynomial_ma[1:, None]
                    )
                else:
                    design = self.design.real.astype(params.dtype)
                    design[self.design_ma_params_idx] = (
                        reduced_polynomial_ma[1:, None]
                    )
                    self.design = design

        # State covariance matrix
        if self.k_posdef > 0:
            if self.state_cov.dtype == params.dtype:
                self.state_cov[0, 0] = params_variance
                if self.state_regression and self.time_varying_regression:
                    self.state_cov[self._exog_variance_idx] = params_exog_variance[:, None]
            else:
                state_cov = self.state_cov.real.astype(params.dtype)
                state_cov[0, 0] = params_variance
                if self.state_regression and self.time_varying_regression:
                    state_cov[self._exog_variance_idx] = params_exog_variance[:, None]
                self.state_cov = state_cov

        # Initialize
        if not self._manual_initialization:
            self.initialize_state()


class SARIMAXResults(StatespaceResults):
    def __init__(self, model, kalman_filter, *args, **kwargs):
        super(SARIMAXResults, self).__init__(model, kalman_filter, *args,
                                             **kwargs)

        # Set additional model parameters
        self.k_seasons = self.model.k_seasons
        self.measurement_error = self.model.measurement_error
        self.time_varying_regression = self.model.time_varying_regression
        self.mle_regression = self.model.mle_regression
        self.simple_differencing = self.model.simple_differencing
        self.enforce_stationarity = self.model.enforce_stationarity
        self.enforce_invertibility = self.model.enforce_invertibility
        self.hamilton_representation = self.model.hamilton_representation

        # Model order
        self.k_diff = self.model.k_diff
        self.k_seasonal_diff = self.model.k_seasonal_diff
        self.k_ar = self.model.k_ar
        self.k_ma = self.model.k_ma
        self.k_seasonal_ar = self.model.k_seasonal_ar
        self.k_seasonal_ma = self.model.k_seasonal_ma

        # Param Numbers
        self.k_ar_params = self.model.k_ar_params
        self.k_ma_params = self.model.k_ma_params

        # Trend / Regression
        self.trend = self.model.trend
        self.k_trend = self.model.k_trend
        self.k_exog = self.model.k_exog

        self.mle_regression = self.model.mle_regression
        self.state_regression = self.model.state_regression

        # Polynomials
        self.polynomial_trend = self.model.polynomial_trend
        self.polynomial_ar = self.model.polynomial_ar
        self.polynomial_ma = self.model.polynomial_ma
        self.polynomial_seasonal_ar = self.model.polynomial_seasonal_ar
        self.polynomial_seasonal_ma = self.model.polynomial_seasonal_ma
        self.polynomial_reduced_ar = np.r_[1, -np.polymul(
            self.polynomial_ar, self.polynomial_seasonal_ar
        )]
        self.polynomial_reduced_ma = np.r_[1, np.polymul(
            self.polynomial_ma, self.polynomial_seasonal_ma
        )]

        # Distinguish parameters
        self.model_orders = self.model.model_orders
        self.params_included = self.model.params_included
        start = end = 0
        for name in self.params_included:
            end += self.model_orders[name]
            setattr(self, '_params_%s' % name, self.params[start:end])
            start += self.model_orders[name]

    @cache_readonly
    def arroots(self):
        return np.roots(self.polynomial_reduced_ar)**-1

    @cache_readonly
    def maroots(self):
        return np.roots(self.polynomial_reduced_ma)**-1

    @cache_readonly
    def arfreq(self):
        z = self.arroots
        if not z.size:
            return
        return np.arctan2(z.imag, z.real) / (2*np.pi)

    @cache_readonly
    def mafreq(self):
        z = self.maroots
        if not z.size:
            return
        return np.arctan2(z.imag, z.real) / (2*np.pi)

    @cache_readonly
    def arparams(self):
        return self._params_ar

    @cache_readonly
    def maparams(self):
        return self._params_ma

    def predict(self, start=None, end=None, exog=None, dynamic=False,
                alpha=.05, *args, **kwargs):
        if start is None:
                start = 0

        # Handle end (e.g. date)
        _start = self.model._get_predict_start(start)
        _end, _out_of_sample = self.model._get_predict_end(end)

        # Handle exogenous parameters
        if _out_of_sample and self.mle_regression or self.state_regression:
            if exog is None:
                raise ValueError('Out-of-sample forecasting in a model with'
                                 ' a regression component requires additional'
                                 ' exogenous values via the `exog` argument')
            exog = np.array(exog)
            required_exog_shape = (_out_of_sample, self.k_exog)
            if not exog.shape == required_exog_shape:
                raise ValueError('Provided exogenous values are not of the'
                                 ' appropriate shape. Required %s, got %s.' %
                                 (str(required_exog_shape), str(exog.shape)))

            # Create a new faux SARIMAX model for the extended dataset
            endog = np.zeros((self.model.orig_endog.shape[0]+_out_of_sample, self.k_endog))
            exog = np.c_[self.model.orig_exog.T, exog.T].T
            model = SARIMAX(
                endog,
                exog=exog,
                order=(self.k_ar, self.k_diff, self.k_ma),
                seasonal_order=(self.k_seasonal_ar, self.k_seasonal_diff,
                                self.k_seasonal_ma, self.k_seasons),
                trend=self.trend,
                measurement_error=self.measurement_error,
                time_varying_regression=self.time_varying_regression,
                mle_regression=self.mle_regression,
                simple_differencing=self.simple_differencing,
                enforce_stationarity=self.enforce_stationarity,
                enforce_invertibility=self.enforce_invertibility,
                hamilton_representation=self.hamilton_representation
            )
            model.update(self.params)

            # Set the kwargs with the update time-varying state space
            # representation matrices
            for name in self.shapes.keys():
                if name == 'obs':
                    continue
                mat = getattr(model, name)
                if mat.shape[-1] > 1:
                    if len(mat.shape) == 2:
                        kwargs[name] = mat[:, -_out_of_sample:]
                    else:
                        kwargs[name] = mat[:, :, -_out_of_sample:]
        elif exog is not None:
            warn('Exogenous array provided to predict, but additional data not'
                 ' required. `exog` argument ignored.')

        return super(SARIMAXResults, self).predict(
            start=start, end=end, exog=exog, dynamic=dynamic, alpha=alpha,
            *args, **kwargs
        )

    def forecast(self, steps=1, exog=None, alpha=.05, *args, **kwargs):
        return super(SARIMAXResults, self).forecast(
            steps, exog=exog, alpha=alpha, *args, **kwargs
        )

    def summary(self, alpha=.05, start=None, *args, **kwargs):
        # Create the model name

        # See if we have an ARIMA component
        order = ''
        if self.k_ar + self.k_diff + self.k_ma > 0:
            if self.k_ar == self.k_ar_params:
                order_ar = self.k_ar
            else:
                order_ar = tuple(self.polynomial_ar.nonzero()[0][1:])
            if self.k_ma == self.k_ma_params:
                order_ma = self.k_ma
            else:
                order_ma = tuple(self.polynomial_ma.nonzero()[0][1:])
            order = '(%s, %d, %s)' % (order_ar, self.k_diff, order_ma)
        # See if we have an SARIMA component
        seasonal_order = ''
        if self.k_seasonal_ar + self.k_seasonal_diff + self.k_seasonal_ma > 0:
            if self.k_ar == self.k_ar_params:
                order_seasonal_ar = int(self.k_seasonal_ar / self.k_seasons)
            else:
                order_seasonal_ar = tuple(self.polynomial_seasonal_ar.nonzero()[0][1:])
            if self.k_ma == self.k_ma_params:
                order_seasonal_ma = int(self.k_seasonal_ma / self.k_seasons)
            else:
                order_seasonal_ma = tuple(self.polynomial_seasonal_ma.nonzero()[0][1:])
            seasonal_order = ('(%s, %d, %s, %d)' %
                              (str(order_seasonal_ar), self.k_seasonal_diff,
                               str(order_seasonal_ma), self.k_seasons))
            if not order == '':
                order += 'x'
        model = ('%s%s%s' %
                 (self.model.__class__.__name__, order, seasonal_order))
        kwargs.setdefault('model', model)
        return super(SARIMAXResults, self).summary(
            alpha=alpha, start=start, *args, **kwargs
        )
