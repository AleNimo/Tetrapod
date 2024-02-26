import numpy as np
from CoppeliaSocket import CoppeliaSocket

class Environment:
    def __init__(self, obs_dim, act_dim, rwd_dim):
        '''
        Creates a 3D environment using CoppeliaSim, where an agent capable of choosing its joints' angles tries to find
        the requested destination.
        :param obs_dim: Numpy array's shape of the observed state
        :param act_dim: Numpy array's shape of the action
        :param dest_pos: Destination position that the agent should search
        '''
        self.name = "ComplexAgentSAC"
        self.obs_dim = obs_dim                        # Observation dimension
        self.act_dim = act_dim                        # Action dimension
        self.rwd_dim = rwd_dim                        # Reward dimension
        self.__pos_size = 2                                     # Position's size
        self.__end_cond = 2.5                                   # End condition
        self.__obs = np.zeros((1,self.obs_dim))           # Observed state
        self.__coppelia = CoppeliaSocket(obs_dim+5)     # Socket to the simulated environment

        #Parameters for forward velocity reward
        self.forward_velocity_reward = 0
        self.__target_velocity = 0.1 # m/s (In the future it could be a changing velocity)
        self.__vmax = 1
        self.__delta_vel = 0.2
        self.__vmin = -1.5

        self.__curvature_forward_vel = - 2* self.__vmax / (self.__delta_vel * self.__vmin)

        #Parameters for forward acceleration penalization
        self.forward_acc_penalty = 0
        self.__max_acc = 8 #m/s^2  (Acceleration at which the penalization is -1)

        #Parameters for lateral velocity penalization
        self.lateral_velocity_penalty = 0
        self.__vmin_lat = -2
        self.__curvature_lateral = 3
        
        #Parameters for orientation reward
        self.orientation_reward = 0
        self.__vmax_ori = 1
        self.__vmin_ori = -1
        self.__curvature_pos = 30
        self.__curvature_neg = 0.5
        self.__neutralAngle = 5 * np.pi/180

            #Auxiliary parameters to simplify expressions
        self.__b1 = -np.exp(-self.__curvature_pos*self.__neutralAngle)
        self.__b2 = -np.exp(-self.__curvature_neg*self.__neutralAngle)

        self.__k1 = self.__vmax_ori/(1+self.__b1)
        self.__k2 = self.__vmin_ori/(np.exp(-self.__curvature_neg*np.pi)+self.__b2)
        
        #Parameters for flat back reward
        self.flat_back_reward = np.zeros(2)
        self.__vmin_back = -2
        self.__curvature_back = 2

        #Reward for not flipping over
        self.__not_flipping_reward = 0.5

    def reset(self):
        ''' Generates and returns a new observed state for the environment (outside of the termination condition) '''
        # Start position in (0,0) and random orientation (in z axis)
        pos = np.zeros(2)
        z_ang = 2*np.random.rand(1) - 1 #vector of 1 rand between -1 and 1, later multiplied by pi
        
        # Join position and angle in one vector
        pos_angle = np.concatenate((pos,z_ang))

        # Reset the simulation environment and obtain the new state
        self.__step = 0
        self.__obs = self.__coppelia.reset(pos_angle)
        return np.copy(self.__obs)

    def set_pos(self, pos):
        ''' Sets and returns a new observed state for the environment '''
        # Reset the simulation environment and obtain the new state
        self.__obs = self.__coppelia.reset(pos.reshape(-1))
        return np.copy(self.__obs)

    def get_pos(self):
        ''' Returns the current position of the agent in the environment '''
        # Return the position
        return self.__obs[0:self.__pos_size]

    def act(self, act):
        ''' Simulates the agent's action in the environment, computes and returns the environment's next state, the
        obtained reward and the termination condition status '''
        # Take the requested action in the simulation and obtain the new state
        next_obs = self.__coppelia.act(act.reshape(-1))
        # Compute the reward
        reward, end = self.compute_reward_and_end(self.__obs.reshape(1,-1), next_obs.reshape(1,-1))
        # Update the observed state
        self.__obs[:] = next_obs
        # Return the environment's next state, the obtained reward and the termination condition status
        return next_obs, reward, end

    def compute_reward(self, obs):
        reward, _ = self.compute_reward_and_end(obs[0:-1], obs[1:])
        return reward

    def compute_reward_and_end(self, obs, next_obs):
        # Compute reward for every individual transition (state -> next_state)

            # Final distance to evaluate end condition
        dist_fin = np.sqrt(np.sum(np.square(next_obs[:,0:self.__pos_size]), axis=1, keepdims=True))

            # Velocity vector from every state observed
        forward_velocity = next_obs[:,9]
        lateral_velocity = next_obs[:,10]

        max_forward_acceleration = next_obs[:,11]

            # Empty vectors to store reward and end flags for every transition
        reward, end = np.zeros((obs.shape[0], self.rwd_dim)), np.zeros((obs.shape[0], 1))

        for i in range(obs.shape[0]):

            '''Reward for forward velocity reaching target velocity'''
            if forward_velocity[i] > 0:
                self.forward_velocity_reward = (self.__vmax - self.__vmin)/(self.__curvature_forward_vel * np.abs(self.__target_velocity - forward_velocity[i]) + 1) + self.__vmin
            else:
                self.forward_velocity_reward = self.__vmax / self.__target_velocity * forward_velocity[i]

            # print("forward_velocity = ", forward_velocity[i])
            # print("forward_velocity_penalty = ", forward_velocity_penalty)

            reward[i,0] = self.forward_velocity_reward

            '''Penalization for peak abs forward acceleration'''
            self.forward_acc_penalty = -1/self.__max_acc * max_forward_acceleration

            reward[i,1] = self.forward_acc_penalty

            '''Penalization for Lateral velocity'''
            self.lateral_velocity_penalty = -self.__vmin_lat/(self.__curvature_lateral * np.abs(lateral_velocity[i]) + 1) + self.__vmin_lat

            # print("lateral_velocity = ", lateral_velocity[i])
            # print("lateral_velocity_penalty = ", lateral_velocity_penalty)

            reward[i,2] = self.lateral_velocity_penalty

            '''Penalization for Orientation deviating from target direction'''
            # Compute angle between agents orientation and target direction based on cosine and sine from coppelia:
            angle_agent2target = np.abs(np.arctan2(next_obs[i,8], next_obs[i,7]))

            # Compute reward based on angle:
            if angle_agent2target < self.__neutralAngle:
                self.orientation_reward = self.__k1*(np.exp(-self.__curvature_pos * angle_agent2target) + self.__b1)
            else:
                self.orientation_reward = self.__k2*(np.exp(-self.__curvature_neg * angle_agent2target) + self.__b2)

            reward[i,3] = self.orientation_reward

            '''Flat Back relative reward: pitch and roll close to 0°'''
            for j in range(5, 7):
                back_angle = np.abs(next_obs[i, j])*np.pi    #angle (in rad) of the back with respect to 0° (horizontal position)

                self.flat_back_reward[j-5] = (-self.__vmin_back/(self.__curvature_back * back_angle + 1) + self.__vmin_back)
                
                reward[i,4] += self.flat_back_reward[j-5]

                # print("back_angle ({0}) = {1:.2f}".format(j, back_angle))
                # print("Flat_back_reward ({0}) = {1:.2f}".format(j, flat_back_reward))
            
            # print("Total_reward = ", reward[i])
                
            '''Reward for avoiding critical failure (flipping over)'''
            reward[i,5] = self.__not_flipping_reward

            #If the absolute value of X or Y angle is greater than 50° there is a penalization and the episode ends
            if abs(next_obs[i, 5]) >= 0.278 or abs(next_obs[i, 6]) >= 0.278:
                reward[i] -= self.__not_flipping_reward
                end[i] = True

            elif dist_fin[i] >= self.__end_cond:
                end[i] = True
                # print("finaliza")
            else:
                end[i] = False

            # print("reward = ", reward[i])
            # print("end = ", end[i])

        return reward, end

    def max_ret(self, obs):
        ''' Computes and returns the maximum return for the state '''
        return 100*np.sqrt(np.sum(np.square(obs.reshape(1,-1)[:,0:self.__pos_size]), axis=1, keepdims=True))