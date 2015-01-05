"""
State Space Model

Author: Chad Fulton
License: Simplified-BSD
"""
from __future__ import division, absolute_import, print_function

import numpy as np
import pandas as pd
from scipy.stats import norm
from .kalman_filter import FilterResults

import statsmodels.tsa.base.tsa_model as tsbase
from .model import Model
from statsmodels.tools.numdiff import approx_hess_cs, approx_fprime_cs
from statsmodels.tools.decorators import cache_readonly, resettable_cache

class MLEModel(Model):
    """
    State space maximum likelihood model

    Parameters
    ----------
    endog : array_like
        The observed time-series process :math:`y`
    k_states : int
        The dimension of the unobserved state process.
    exog : array_like, optional
        Array of exogenous regressors, shaped nobs x k. Default is no
        exogenous regressors.
    dates : array-like of datetime, optional
        An array-like object of datetime objects. If a Pandas object is given
        for endog, it is assumed to have a DateIndex.
    freq : str, optional
        The frequency of the time-series. A Pandas offset or 'B', 'D', 'W',
        'M', 'A', or 'Q'. This is optional if dates are given.

    Attributes
    ----------
    start_params : array
        Starting parameters for maximum likelihood estimation.
    params_names : list of str
        List of human readable parameter names (for parameters actually
        included in the model).
    model_names : list of str
        The plain text names of all possible model parameters.
    model_latex_names : list of str
        The latex names of all possible model parameters.

    See Also
    --------
    statsmodels.tsa.statespace.Model
    statsmodels.tsa.statespace.KalmanFilter
    """
    def __init__(self, endog, k_states, exog=None, dates=None, freq=None,
                 *args, **kwargs):
        # Set the default results class to be MLEResults
        kwargs.setdefault('results_class', MLEResults)

        super(MLEModel, self).__init__(endog, k_states, exog, dates, freq,
                                       *args, **kwargs)
        
        # Initialize the parameters
        self.params = None

    def fit(self, start_params=None, transformed=True,
            method='lbfgs', maxiter=50, full_output=1,
            disp=5, callback=None, return_params=False,
            bfgs_tune=False, *args, **kwargs):
        """
        Fits the model by maximum likelihood via Kalman filter.

        Parameters
        ----------
        start_params : array_like, optional
            Initial guess of the solution for the loglikelihood maximization.
            If None, the default is given by Model.start_params.
        method : str, optional
            The `method` determines which solver from `scipy.optimize`
            is used, and it can be chosen from among the following strings:

            - 'newton' for Newton-Raphson, 'nm' for Nelder-Mead
            - 'bfgs' for Broyden-Fletcher-Goldfarb-Shanno (BFGS)
            - 'lbfgs' for limited-memory BFGS with optional box constraints
            - 'powell' for modified Powell's method
            - 'cg' for conjugate gradient
            - 'ncg' for Newton-conjugate gradient
            - 'basinhopping' for global basin-hopping solver

            The explicit arguments in `fit` are passed to the solver,
            with the exception of the basin-hopping solver. Each
            solver has several optional arguments that are not the same across
            solvers. See the notes section below (or scipy.optimize) for the
            available arguments and for the list of explicit arguments that the
            basin-hopping solver supports.
        maxiter : int, optional
            The maximum number of iterations to perform.
        full_output : boolean, optional
            Set to True to have all available output in the Results object's
            mle_retvals attribute. The output is dependent on the solver.
            See LikelihoodModelResults notes section for more information.
        disp : boolean, optional
            Set to True to print convergence messages.
        callback : callable callback(xk), optional
            Called after each iteration, as callback(xk), where xk is the
            current parameter vector.
        return_params : boolean, optional
            Whether or not to return only the array of maximizing parameters.
            Default is False.
        bfgs_tune : boolean, optional
            BFGS methods by default use internal methods for approximating the
            score and hessian by finite differences. If `bfgs_tune=True` the
            maximizing parameters from the BFGS method are used as starting
            parameters for a second round of maximization using complex-step
            differentiation. Has no effect for other methods. Default is False.

        Returns
        -------
        MLEResults

        See also
        --------
        statsmodels.base.model.LikelihoodModel.fit : for more information
            on using the solvers.
        MLEResults : results class returned by fit
        """

        if start_params is None:
            start_params = self.start_params
            transformed = True

        # Unconstrain the starting parameters
        if transformed:
            start_params = self.untransform_params(np.array(start_params))

        if method == 'lbfgs' or method == 'bfgs':
            kwargs.setdefault('approx_grad', True)
            kwargs.setdefault('epsilon', 1e-5)

        # Maximum likelihood estimation
        # Set the optional arguments for the loglikelihood function to
        # maximize the average loglikelihood, by default.
        fargs = (kwargs.get('average_loglike', True), False, False)
        mlefit = super(MLEModel, self).fit(start_params, method=method,
                                        fargs=fargs,
                                        maxiter=maxiter,
                                        full_output=full_output, disp=disp,
                                        callback=callback, **kwargs)

        # Optionally tune the maximum likelihood estimates using complex step
        # gradient
        if bfgs_tune and method == 'lbfgs' or method == 'bfgs':
            kwargs['approx_grad'] = False
            del kwargs['epsilon']
            fargs = (kwargs.get('average_loglike', True), False, False)
            mlefit = super(MLEModel, self).fit(mlefit.params, method=method,
                                            fargs=fargs,
                                            maxiter=maxiter,
                                            full_output=full_output, disp=disp,
                                            callback=callback, **kwargs)

        # Constrain the final parameters and update the model to be sure we're
        # using them (in case, for example, the last time update was called
        # via the optimizer it was a gradient calculation, etc.)
        self.update(mlefit.params, transformed=False)

        # Just return the fitted parameters if requested
        if return_params:
            self.filter(return_loglike=True)
            return self.params
        # Otherwise construct the results class if desired
        else:
            res = self.filter()
            res.mlefit = mlefit
            res.mle_retvals = mlefit.mle_retvals
            res.mle_settings = mlefit.mle_settings
            return res

    def loglike(self, params=None, average_loglike=False, transformed=True,
                set_params=True, *args, **kwargs):
        """
        Loglikelihood evaluation

        Parameters
        ----------
        params : array_like, optional
            Array of parameters at which to evaluate the loglikelihood
            function.
        average_loglike : boolean, optional
            Whether or not to return the average loglikelihood (rather than
            the sum of loglikelihoods across all observations). Default is
            False.
        transformed : boolean, optional
            Whether or not `params` is already transformed. Default is True.
        set_params : boolean
            Whether or not to copy `params` to the model object's params
            attribute. Default is True.

        Notes
        -----
        [1]_ recommend maximizing the average likelihood to avoid scale issues;
        this can be achieved by setting `average_loglike=True`.

        References
        ----------
        .. [1] Koopman, Siem Jan, Neil Shephard, and Jurgen A. Doornik. 1999.
           Statistical Algorithms for Models in State Space Using SsfPack 2.2.
           Econometrics Journal 2 (1): 107-60. doi:10.1111/1368-423X.00023.

        See Also
        --------
        update : modifies the internal state of the Model to reflect new params
        """
        if params is not None:
            self.update(params, transformed=transformed, set_params=set_params)

        # By default, we do not need to consider recreating the entire
        # _statespace and Cython Kalman filter objects because only parameters
        # will be changing and not dimensions of matrices.
        kwargs.setdefault('recreate', False)

        loglike = super(MLEModel, self).loglike(*args, **kwargs)

        # Koopman, Shephard, and Doornik recommend maximizing the average
        # likelihood to avoid scale issues.
        if average_loglike:
            return loglike / self.nobs
        else:
            return loglike

    def score(self, params, *args, **kwargs):
        """
        Compute the score function at params.

        Parameters
        ----------
        params : array_like
            Array of parameters at which to evaluate the score.

        Returns
        ----------
        score : array
            Score, evaluated at `params`.

        Notes
        -----
        This is a numerical approximation.
        """
        nargs = len(args)
        if nargs < 1:
            kwargs.setdefault('average_loglike', True)
        if nargs < 2:
            kwargs.setdefault('transformed', False)
        if nargs < 3:
            kwargs.setdefault('set_params', False)
        return approx_fprime_cs(params, self.loglike, epsilon=1e-9, args=args, kwargs=kwargs)

    def hessian(self, params, *args, **kwargs):
        """
        Hessian matrix of the likelihood function, evaluated at the given
        parameters.

        Parameters
        ----------
        params : array_like
            Array of parameters at which to evaluate the hessian.

        Returns
        -------
        hessian : array
            Hessian matrix evaluated at `params`

        Notes
        -----
        This is a numerical approximation.
        """
        nargs = len(args)
        if nargs < 1:
            kwargs.setdefault('average_loglike', True)
        if nargs < 2:
            kwargs.setdefault('transformed', False)
        if nargs < 3:
            kwargs.setdefault('set_params', False)
        return approx_hess_cs(params, self.loglike, epsilon=1e-9, args=args, kwargs=kwargs)

    @property
    def start_params(self):
        if hasattr(self, '_start_params'):
            return self._start_params
        else:
            raise NotImplementedError
    @start_params.setter
    def start_params(self, values):
        self._start_params = np.asarray(values)

    @property
    def params_names(self):
        return self.model_names

    @property
    def model_names(self):
        return self._get_model_names(latex=False)

    @property
    def model_latex_names(self):
        return self._get_model_names(latex=True)

    def _get_model_names(self, latex=False):
        if latex:
            names = ['param_%d' % i for i in range(len(self.start_params))]
        else:
            names = ['param.%d' % i for i in range(len(self.start_params))]
        return names

    def transform_jacobian(self, unconstrained):
        """
        Jacobian matrix for the parameter transformation function

        Parameters
        ----------
        unconstrained : array_like
            Array of unconstrained parameters used by the optimizer.

        Returns
        -------
        jacobian : array
            Jacobian matrix of the transformation, evaluated at `unconstrained`

        Notes
        -----
        This is a numerical approximation.

        See Also
        --------
        transform_params
        """
        return approx_fprime_cs(unconstrained, self.transform_params)

    def transform_params(self, unconstrained):
        """
        Transform unconstrained parameters used by the optimizer to constrained
        parameters used in likelihood evaluation

        Parameters
        ----------
        unconstrained : array_like
            Array of unconstrained parameters used by the optimizer, to be
            transformed.

        Returns
        -------
        constrained : array_like
            Array of constrained parameters which may be used in likelihood
            evalation.
        
        Notes
        -----
        This is a noop in the base class, subclasses should override where
        appropriate.
        """
        return unconstrained

    def untransform_params(self, constrained):
        """
        Transform constrained parameters used in likelihood evaluation
        to unconstrained parameters used by the optimizer

        Parameters
        ----------
        constrained : array_like
            Array of constrained parameters used in likelihood evalution, to be
            transformed.

        Returns
        -------
        unconstrained : array_like
            Array of unconstrained parameters used by the optimizer.

        Notes
        -----
        This is a noop in the base class, subclasses should override where
        appropriate.
        """
        return constrained

    def update(self, params, transformed=True, set_params=True):
        """
        Update the parameters of the model

        Parameters
        ----------
        params : array_like
            Array of new parameters.
        transformed : boolean, optional
            Whether or not `params` is already transformed. If set to False,
            `transform_params` is called. Default is True.
        set_params : boolean
            Whether or not to copy `params` to the model object's params
            attribute. Usually is set to True unless a subclass has additional
            defined behavior in the case it is False (otherwise this is a noop
            except for possibly transforming the parameters). Default is True.

        Returns
        -------
        params : array_like
            Array of parameters.

        Notes
        -----
        Since Model is a base class, this method should be overridden by
        subclasses to perform actual updating steps.
        """
        params = np.array(params)

        if not transformed:
            params = self.transform_params(params)
        if set_params:
            self.params = params
        return params

    @classmethod
    def from_formula(cls, formula, data, subset=None, *args, **kwargs):
        """
        Not implemented for State space models
        """
        raise NotImplementedError


class MLEResults(FilterResults, tsbase.TimeSeriesModelResults):
    """
    Class to hold results from fitting a state space model.

    Parameters
    ----------
    model : Model instance
        The fitted model instance

    Attributes
    ----------
    aic : float
        Akaike Information Criterion
    bic : float
        Bayes Information Criterion
    bse : array
        The standard errors of the parameters. Computed using the numerical Hessian.
    cov_params : array
        The variance / covariance matrix. Computed using the numerical Hessian.
    hqic : array
        Hannan-Quinn Information Criterion
    llf : float
        The value of the log-likelihood function evaluated at `params`.
    model : Model instance
        A reference to the model that was fit.
    nobs : float
        The number of observations used to fit the model.
    params : array
        The parameters of the model.
    pvalues : array
        The p-values associated with the z-statistics of the coefficients.
        Note that the coefficients are assumed to have a Normal distribution.
    scale : float
        This is currently set to 1.0 and not used by the model or its results.
    sigma2 : float
        The variance of the residuals.
    zvalues : array
        The z-statistics for the coefficients.

    Methods
    -------
    conf_int
    f_test
    fittedvalues
    forecast
    load
    predict
    remove_data
    resid
    save
    summary
    t_test
    wald_test
    """
    def __init__(self, model, *args, **kwargs):
        self.data = model.data

        # Save the model output
        self._endog_names = model.endog_names
        self._exog_names = model.endog_names
        self._params = model.params
        self._params_names = model.params_names
        self._model_names = model.model_names
        self._model_latex_names = model.model_latex_names

        # Associate the names with the true parameters
        params = pd.Series(self._params, index=self._params_names)

        # Initialize the Statsmodels model base
        tsbase.TimeSeriesModelResults.__init__(self, model, params,
                                               normalized_cov_params=None,
                                               scale=1., *args, **kwargs)

        # Initialize the statespace representation
        super(MLEResults, self).__init__(model)

        # Setup the cache
        self._cache = resettable_cache()

    @cache_readonly
    def aic(self):
        return -2*self.llf + 2*self.params.shape[0]

    @cache_readonly
    def bic(self):
        return -2*self.llf + self.params.shape[0]*np.log(self.nobs)

    @cache_readonly
    def bse(self):
        return np.sqrt(np.diagonal(self.cov_params))

    @cache_readonly
    def cov_params(self):
        # Uses Delta method (method of propagation of errors)

        unconstrained = self.model.untransform_params(self._params)
        jacobian = self.model.transform_jacobian(unconstrained)
        hessian = self.model.hessian(unconstrained, set_params=False)

        # Reset the matrices to the saved parameters (since they were
        # overwritten in the hessian call)
        self.model.update(self.model.params)

        return jacobian.dot(-np.linalg.inv(hessian*self.nobs)).dot(jacobian.T)

    def fittedvalues(self):
        """The predicted values of the model."""
        return self.forecasts.copy()

    @cache_readonly
    def hqic(self):
        return -2*self.llf + 2*np.log(np.log(self.nobs))*self.params.shape[0]

    @cache_readonly
    def llf(self):
        return self.loglikelihood[self.loglikelihood_burn:].sum()

    @cache_readonly
    def pvalues(self):
        return norm.sf(np.abs(self.zvalues)) * 2

    def resid(self):
        """The model residuals."""
        return self.forecasts_error.copy()

    @cache_readonly
    def zvalues(self):
        return self.params / self.bse

    def predict(self, start=None, end=None, dynamic=False, alpha=.05,
                full_results=False, *args, **kwargs):
        """
        In-sample prediction and out-of-sample forecasting

        Parameters
        ----------
        start : int, str, or datetime, optional
            Zero-indexed observation number at which to start forecasting, ie.,
            the first forecast is start. Can also be a date string to
            parse or a datetime type. Default is the the zeroth observation.
        end : int, str, or datetime, optional
            Zero-indexed observation number at which to end forecasting, ie.,
            the first forecast is start. Can also be a date string to
            parse or a datetime type. However, if the dates index does not
            have a fixed frequency, end must be an integer index if you
            want out of sample prediction. Default is the last observation in
            the sample.
        dynamic : int or boolean or None, optional
            Specifies the number of steps ahead for each in-sample prediction.
            If not specified, then in-sample predictions are one-step-ahead.
            False and None are interpreted as 0. Default is False.
        alpha : float, optional
            The confidence intervals for the forecasts are (1 - alpha) %.
            Default is 0.05.
        full_results : boolean, optional
            If True, returns a FilterResults instance; if False returns a
            tuple with forecasts, the forecast errors, and the forecast error
            covariance matrices. Default is False.

        Returns
        -------
        forecast : array
            Array of out of sample forecasts.
        forecasts_error_cov : array
            Array of the covariance matrices of the forecasts.
        confidence_intervals : array
            Array (2-dim) of the confidence interval for the forecasts.
        index : array or pandas.DateTimeIndex
            Array of indices for forecasts; either integers or dates, depending
            on the type of `endog`.
        """
        if start is None:
            start = 0

        # Handle start and end (e.g. dates)
        start = self.model._get_predict_start(start)
        end, out_of_sample = self.model._get_predict_end(end)

        # Perform the prediction
        res = super(MLEResults, self).predict(
            start, end+out_of_sample+1, dynamic, full_results, *args, **kwargs
        )

        if full_results:
            return res
        else:
            (forecasts, forecasts_error, forecasts_error_cov) = res

        # Calculate the confidence intervals
        critical_value = norm.ppf(1 - alpha / 2.)
        std_errors = np.sqrt(forecasts_error_cov.diagonal().T)
        confidence_intervals = np.c_[
            (forecasts - critical_value*std_errors)[:, :, None],
            (forecasts + critical_value*std_errors)[:, :, None],
        ]

        # Return the dates if we have them
        index = np.arange(start, end+out_of_sample+1)
        if hasattr(self.data, 'predict_dates'):
            index = self.data.predict_dates
            if(isinstance(index, pd.DatetimeIndex)):
                index = index._mpl_repr()

        return forecasts, forecasts_error_cov, confidence_intervals, index

    def forecast(self, steps=1, alpha=.05, *args, **kwargs):
        """
        Out-of-sample forecasts

        Parameters
        ----------
        steps : int, optional
            The number of out of sample forecasts from the end of the
            sample. Default is 1.
        alpha : float, optional
            The confidence intervals for the forecasts are (1 - alpha) %.
            Default is 0.05.

        Returns
        -------
        forecast : array
            Array of out of sample forecasts.
        forecasts_error_cov : array
            Array of the covariance matrices of the forecasts.
        confidence_intervals : array
            Array (2-dim) of the confidence interval for the forecasts.
        index : array or pandas.DateTimeIndex
            Array of indices for forecasts; either integers or dates, depending
            on the type of `endog`.
        """
        return self.predict(start=self.nobs, end=self.nobs+steps-1, alpha=alpha,
                            *args, **kwargs)

    def summary(self, alpha=.05, start=None, *args, **kwargs):
        """
        Summarize the Model

        Parameters
        ----------
        alpha : float, optional
            Significance level for the confidence intervals. Default is 0.05.
        start : int, optional
            Integer of the start observation. Default is 0.

        Returns
        -------
        summary : Summary instance
            This holds the summary table and text, which can be printed or
            converted to various output formats.

        See Also
        --------
        statsmodels.iolib.summary.Summary
        """
        from statsmodels.iolib.summary import Summary
        model = self.model
        title = 'Statespace Model Results'

        if start is None:
            start = 0
        if self.data.dates is not None:
            dates = self.data.dates
            d = dates[start]
            sample = ['%02d-%02d-%02d' % (d.month, d.day, d.year)]
            d = dates[-1]
            sample += ['- ' + '%02d-%02d-%02d' % (d.month, d.day, d.year)]                
        else:
            sample = [str(start), ' - ' + str(self.model.nobs)]

        top_left = [
            ('Dep. Variable:', None),
            ('Model:', [kwargs.get('model', model.__class__.__name__)]),
            ('Date:', None),
            ('Time:', None),
            ('Sample:', [sample[0]]),
            ('', [sample[1]])
        ]

        top_right = [
            ('No. Observations:', [self.model.nobs]),
            ('Log Likelihood', ["%#5.3f" % self.llf]),
            ('AIC', ["%#5.3f" % self.aic]),
            ('BIC', ["%#5.3f" % self.bic]),
            ('HQIC', ["%#5.3f" % self.hqic])
        ]

        summary = Summary()
        summary.add_table_2cols(self, gleft=top_left, gright=top_right,
                                title=title)
        summary.add_table_params(self, alpha=alpha, xname=self._params_names,
                                 use_t=False)

        return summary
