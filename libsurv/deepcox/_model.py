import os
import tensorflow as tf

from . import _check_config, _check_surv_data, _prepare_surv_data
from ..utils import plot_train_curve, concordance_index

class model(object):
    """docstring for model"""
    def __init__(self, input_nodes, hidden_layers_nodes, config={}):
        """
        DeepCox Neural Network Class Constructor.

        Parameters
        ----------
        input_nodes: int
            The number of input nodes. It's also equal to the number of features.
        hidden_layers_nodes: list
            Number of nodes in hidden layers of neural network.
        config: dict
            Some configurations or hyper-parameters of neural network.
            Defalt settings is below:
            config = {
                "learning_rate": 0.001,
                "learning_rate_decay": 1.0,
                "activation": "tanh",
                "L2_reg": 0.0,
                "L1_reg": 0.0,
                "optimizer": "sgd",
                "dropout_keep_prob": 1.0,
                "seed": 42
            }
        """
        super(model, self).__init__()
        # neural nodes
        self.input_nodes = input_nodes
        self.hidden_layers_nodes = hidden_layers_nodes
        assert hidden_layers_nodes[-1] == 1
        # network hyper-parameters
        _check_config(config)
        self.config = config
        # graph level random seed
        tf.set_random_seed(config["seed"])
        # some gobal settings
        self.global_step = tf.get_variable('global_step', initializer=tf.constant(0), trainable=False)
        self.keep_prob = tf.placeholder(tf.float32)
        
        # It's the best way to use `tf.placeholder` instead of `tf.data.Dataset`.
        # Since style of `batch` is not appropriate in survival analysis.
        self.X = tf.placeholder(tf.float32, [None, input_nodes], name='X-Input')
        self.Y = tf.placeholder(tf.float32, [None, 1], name='Y-Input')

    def _create_fc_layer(self, x, output_dim, scope):
        with tf.variable_scope(scope, reuse=tf.AUTO_REUSE):
            w = tf.get_variable('weights', [x.shape[1], output_dim], 
                initializer=tf.truncated_normal_initializer(stddev=0.1)
            )

            b = tf.get_variable('biases', [output_dim], 
                initializer=tf.constant_initializer(0.0)
            )

            # add weights and bias to collections
            tf.add_to_collection("var_weight", w)
            tf.add_to_collection("var_bias", b)

            layer_out = tf.nn.dropout(tf.matmul(x, w) + b, self.keep_prob)

            if activation == 'relu':
                layer_out = tf.nn.relu(layer_out)
            elif activation == 'sigmoid':
                layer_out = tf.nn.sigmoid(layer_out)
            elif activation == 'tanh':
                layer_out = tf.nn.tanh(layer_out)
            else:
                raise NotImplementedError('activation not recognized')

            return layer_out

    def _create_network(self):
        """
        Define the neural network that only includes FC layers.
        """
        with tf.name_scope("hidden_layers"):
            cur_x = self.X
            for i, num_nodes in enumerate(self.hidden_layers_nodes):
                cur_x = self._create_fc_layer(cur_x, num_nodes, "layer"+str(i+1))
            # output of network
            self.Y_hat = cur_x

    def _create_loss(self):
        """
        Define the loss function.

        Notes
        -----
        The loss function definded here is negative log of Breslow Approximation partial 
        likelihood function. See more in "Breslow N., 'Covariance analysis of censored 
        survival data, ' Biometrics 30.1(1974):89-99.".
        """
        with tf.name_scope("loss"):
            # Obtain T and E from self.Y
            # NOTE: negtive value means E = 0
            Y_c = tf.squeeze(self.Y)
            Y_hat_c = tf.squeeze(self.Y_hat)
            Y_label_T = tf.abs(Y_c)
            Y_label_E = tf.cast(tf.greater(Y_c, 0), dtype=tf.float32)

            Y_hat_hr = tf.exp(Y_hat_c)
            Y_hat_cumsum = tf.log(tf.cumsum(Y_hat_hr))
            
            # Start Computation of Loss function

            # Get Segment from T
            unique_values, segment_ids = tf.unique(Y_label_T)
            # Get Segment_max
            loss_s2_v = tf.segment_max(Y_hat_cumsum, segment_ids)
            # Get Segment_count
            loss_s2_count = tf.segment_sum(Y_label_E, segment_ids)
            # Compute S2
            loss_s2 = tf.reduce_sum(tf.multiply(loss_s2_v, loss_s2_count))
            # Compute S1
            loss_s1 = tf.reduce_sum(tf.multiply(Y_hat_c, Y_label_E))
            # Compute Breslow Loss
            loss_breslow = tf.subtract(loss_s2, loss_s1)

            # Compute Regularization Term Loss
            reg_item = tf.contrib.layers.l1_l2_regularizer(self.config["L1_reg"], self.config["L2_reg"])
            loss_reg = tf.contrib.layers.apply_regularization(reg_item, tf.get_collection("var_weight"))

            # Loss function = Breslow Function + Regularization Term
            self.loss = tf.add(loss_breslow, loss_reg)

    def _create_optimizer(self):
        """
        Define optimizer
        """
        # SGD Optimizer
        if self.config["optimizer"] == 'sgd':
            lr = tf.train.exponential_decay(
                self.config["learning_rate"],
                self.global_step,
                1,
                self.config["learning_rate_decay"]
            )
            self.optimizer = tf.train.GradientDescentOptimizer(lr).minimize(self.loss, global_step=self.global_step)
        # Adam Optimizer
        elif self.config["optimizer"] == 'adam':
            self.optimizer = tf.train.AdamOptimizer(self.config["learning_rate"]).minimize(self.loss, global_step=self.global_step)
        elif self.config["optimizer"] == 'rms':
            self.optimizer = tf.train.RMSPropOptimizer(self.config["learning_rate"]).minimize(self.loss, global_step=self.global_step)     
        else:
            raise NotImplementedError('Optimizer not recognized')

    def build_graph(self):
        """Build graph of DeepCox
        """
        self._create_network()
        self._create_loss()
        self._create_optimizer()

    def start_session(self):
        self.sess = tf.Session()

    def close_session(self):
        self.sess.close()
        print("Current session closed.")

    def train(self, data, num_steps, num_skip_steps, load_model="", save_model="", plot=True):
        """
        Training DeepCox model.

        Parameters
        ----------
        data: dict
            Survival dataset follows the format {"X": DataFrame, "Y": DataFrame}.
            It's suggested that you utilize `libsurv.datasets.survival_df` to obtain 
            the DataFrame object and then construct the target dict.
        num_steps: int
            The number of training steps.
        num_skip_steps: int
            The number of skipping training steps. Model would be saved after 
            each `num_skip_steps`.
        load_model: string
            Path for loading model.
        save_model: string
            Path for saving model.
        plot: boolean
            Is plot the learning curve.
        """
        # dataset pre-processing
        self.indices, self.train_data = _prepare_surv_data(data)

        # data to feed
        feed_data = {
            self.keep_prob: self.config['dropout_keep_prob'],
            self.X: self.train_data['X'].values,
            self.Y: self.train_data['Y'].values
        }

        # Session Running
        self.sess.run(tf.global_variables_initializer())
        if load_model != "":
            saver = tf.train.Saver()
            saver.restore(self.sess, load_model)

        # we use this to calculate late average loss in the last SKIP_STEP steps
        total_loss = 0.0
        # Get current global step
        initial_step = self.global_step.eval()
        # Record evaluations during training
        watch_list = {'loss': [], 'metrics': []}
        for index in range(initial_step, initial_step + num_steps):
            y_hat, loss_value, _ = self.sess.run([self.Y_hat, self.loss, self.optimizer], feed_dict=feed_data)
            # append values
            watch_list['loss'].append(loss_value)
            watch_list['metrics'].append(concordance_index(self.train_data['Y'].values, y_hat))
            total_loss += loss_value
            if (index + 1) % num_skip_steps == 0:
                print('Average loss at step {}: {:5.1f}'.format(index, total_loss / num_skip_steps))
                total_loss = 0.0

        # we only save the final trained model
        if save_model != "":
            # defaults to saving all variables
            saver = tf.train.Saver()
            saver.save(self.sess, save_model)
        # plot learning curve
        if plot:
            plot_train_curve(watch_list['loss'], "Loss function")
            plot_train_curve(watch_list['metrics'], "Concordance index")

        return watch_list

    def predict(self, X, output_margin=True):
        """
        Predict log hazard ratio using trained model.

        Parameters
        ----------
        X : DataFrame
            Input data with covariate variables, shape of which is (n, input_nodes).

        Returns
        -------
        np.array
            Predicted log hazard ratio of samples, shape of which is (n, 1). 

        Examples
        --------
        >>> # "array([[0.3], [1.88], [-0.1], ..., [0.98]])"
        >>> model.predict(test_X)
        """
        # we set dropout to 1.0 when making prediction
        log_hr = self.sess.run([self.Y_hat], feed_dict={self.X: X.values, self.keep_prob: 1.0})
        if output_margin == False:
            return np.exp(log_hr)
        return log_hr

    def evals(self, data):
        """
        Evaluate labeled dataset using the CI metrics under current trained model.

        Parameters
        ----------
        data: dict
            Survival dataset follows the format {"X": DataFrame, "Y": DataFrame}.
            It's suggested that you utilize `libsurv.datasets.survival_df` to obtain 
            the DataFrame object and then construct the target dict.

        Returns
        -------
        float
            CI metrics on your dataset.
        """
        _check_surv_data(data)
        preds = self.predict(data['X'])
        return concordance_index(data['Y'].values, preds)

    def predict_survival_function(self, X):
        """
        Predict survival function of samples.

        Parameters
        ----------
        X: DataFrame
            Input data with covariate variables, shape of which is (n, input_nodes).

        Returns
        -------
        np.array
            Predicted survival function of samples, shape of which is (n, #Time_Points). 
        """
        pred_hr = self.predict(X, output_margin=False)
        # TODO
        pass
        