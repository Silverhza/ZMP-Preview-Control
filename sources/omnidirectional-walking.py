import numpy as np 
import scipy.io 
import math
import itertools
import matplotlib.pyplot as plt 
from mpl_toolkits.mplot3d import Axes3D
from pytransform3d.rotations import *
import os
from pytransform3d.trajectories import *
#from tf.transformations import quaternion_from_euler, euler_from_quaternion
from scipy.spatial.transform import Rotation as R
from control.matlab import dare # for solving the discrete algebraic Riccati equation

_EPS = np.finfo(float).eps * 4.0
_AXES2TUPLE = { 
      'sxyz': (0, 0, 0, 0), 'sxyx': (0, 0, 1, 0), 'sxzy': (0, 1, 0, 0), 
      'sxzx': (0, 1, 1, 0), 'syzx': (1, 0, 0, 0), 'syzy': (1, 0, 1, 0), 
      'syxz': (1, 1, 0, 0), 'syxy': (1, 1, 1, 0), 'szxy': (2, 0, 0, 0), 
      'szxz': (2, 0, 1, 0), 'szyx': (2, 1, 0, 0), 'szyz': (2, 1, 1, 0), 
      'rzyx': (0, 0, 0, 1), 'rxyx': (0, 0, 1, 1), 'ryzx': (0, 1, 0, 1), 
      'rxzx': (0, 1, 1, 1), 'rxzy': (1, 0, 0, 1), 'ryzy': (1, 0, 1, 1), 
      'rzxy': (1, 1, 0, 1), 'ryxy': (1, 1, 1, 1), 'ryxz': (2, 0, 0, 1), 
      'rzxz': (2, 0, 1, 1), 'rxyz': (2, 1, 0, 1), 'rzyz': (2, 1, 1, 1)} 
_TUPLE2AXES = dict((v, k) for k, v in _AXES2TUPLE.items())
_NEXT_AXIS = [1, 2, 0, 1] 
# DEBUG = True 

class GaitController():
    def __init__(self):
        self.state = "init"
        # ZMP Preview Parameter
        self.zc = 0.0
        self.dt = 0.0
        self.t_preview = 0
        self.A_d = 0.0
        self.B_d = 0.0
        self.C_d = 0.0
        self.Gi = 0.0
        self.Gx = 0.0
        self.Gd = 0.0

        self.cnt = 1

        # here command for walking 
        # defined as motion vector
        self.cmd_x = 0.05
        self.cmd_y = 0.00
        self.cmd_a = np.radians(10)

        self.sx = 0.0
        self.sy = 0.0
        self.sa = 0.0

        # jarak dari hip ke kaki
        self.hip_offset = 0.037

        # Walking timing parameter
        self.t_step = 0.25
        self.dsp_ratio = 0.15
        self.t_dsp = self.dsp_ratio * self.t_step
        self.t_ssp = (1.0 - self.dsp_ratio) * self.t_step
        self.t = 0
        # 0 : DSP, 1 : SSP
        self.walking_phase = 0

        self.preview_len = 0
        # zmp trajectory dalam bentuk 2d pose
        self.zmp_x = []
        self.zmp_y = []
        self.zmp_a = []

        # ini harusnya 2 vector saja, x, y
        self.footstep = [[0.0,-self.hip_offset,0],
                         [0.0,self.hip_offset,0],
                         [0.0,-self.hip_offset,0]]

        self.support_foot = 1
        # state 
        self.x_x = np.zeros((3,1))
        self.x_y = np.zeros((3,1))
        # self.com_x = 0
        # self.com_y = 0
        # ini nanti isinya x, y, z, qw, qx, qy, qz
        self.com = [0,0,0,0,0,0,0]

        self.t_bez = 0
        self.dt_bez = 0 # ini dikalkulasi pas get param 
        self.max_swing_height = 0.02
        # Inisial dan target pose kaki kiri ketika swing
        # Untuk orientasi yg dipakai hanya yaw saja
        self.init_lfoot_pose = np.zeros((7,1), dtype=float)
        self.init_lfoot_position = np.zeros((3,1), dtype=float)
        self.init_lfoot_orientation_yaw = 0.0 
        self.target_lfoot_pose = np.zeros((7,1), dtype=float)
        self.target_lfoot_position = np.zeros((3,1), dtype=float)
        self.target_lfoot_orientation_yaw = 0.0

        # Inisial dan target pose kaki kanan ketika swing
        self.init_rfoot_pose = np.zeros((7,1), dtype=float)
        self.init_rfoot_position = np.zeros((3,1), dtype=float)
        self.init_rfoot_orientation_yaw = 0.0
        self.target_rfoot_pose = np.zeros((7,1), dtype=float)
        self.target_rfoot_position = np.zeros((3,1), dtype=float)
        self.target_rfoot_orientation_yaw = 0.0
        self.init_com_yaw = 0.0 
        self.target_com_yaw = 0.0
        self.com_yaw = []

        # x, y, z, qw, qx, qy, qz
        self.cur_lfoot = [0,self.hip_offset,0,0,0,0,0]
        self.cur_rfoot = [0,-self.hip_offset,0,0,0,0,0]
        # FIFO to store foot array
        self.left_foot = []
        self.right_foot = []

        self.pattern_ready = False

        self.left_foot_pose = []
        self.right_foot_pose = []

        # INI VARIABLE UNTUK GAIT PARAM
        self.support_x = 0
        self.support_y = 0
        self.body_tilt = 0
        self.foot_y = 0
        
    def calculatePreviewControlParams(self,A, B, C, Q, R, N):
        C_dot_A = C*A
        C_dot_B = C*B

        A_tilde = np.matrix([[1, C_dot_A[0,0], C_dot_A[0,1], C_dot_A[0,2]],
                                [0, A[0,0], A[0,1], A[0,2]],
                                [0, A[1,0], A[1,1], A[1,2]],
                                [0, A[2,0], A[2,1], A[2,2]]])
        B_tilde = np.matrix([[C_dot_B[0,0]],
                                [B[0,0]],
                                [B[1,0]],
                                [B[2,0]]])
        C_tilde = np.matrix([[1, 0, 0, 0]])

        [P_tilde, _, _] = dare(A_tilde, B_tilde, C_tilde.T*Q*C_tilde, R)
        K_tilde = (R + B_tilde.T*P_tilde*B_tilde).I*(B_tilde.T*P_tilde*A_tilde)

        Ks = K_tilde[0, 0]
        Kx = K_tilde[0, 1:]

        Ac_tilde = A_tilde - B_tilde*K_tilde

        G = np.zeros((1, N))

        G[0] = -Ks
        I_tilde = np.matrix([[1],[0],[0],[0]])
        X_tilde = -Ac_tilde.T*P_tilde*I_tilde

        for i in range(N):
            G[0,i] = (R + B_tilde.T*P_tilde*B_tilde).I*(B_tilde.T)*X_tilde
            X_tilde = Ac_tilde.T*X_tilde

        return Ks, Kx, G
    
    # Fungsi untuk mendapatkan parameter walking gait dari mat file
    def get_gait_parameter(self):    
        Q = 10
        R = 1e-6
        g = 9.81
        self.zc = 0.23
        self.dt = 0.01
        self.t_preview = 1.2
        self.preview_len = int(self.t_preview / self.dt)
        self.A_d = np.mat(([1, self.dt, self.dt**2/2],
                [0, 1, self.dt],
                [0, 0, 1]))
        self.B_d = np.mat((self.dt**3/6, self.dt**2/2, self.dt)).T
        self.C_d = np.mat((1, 0, -self.zc/g))
        self.Gi, self.Gx, self.Gd = self.calculatePreviewControlParams(self.A_d, self.B_d, self.C_d, Q, R, self.preview_len)
        self.dt_bez = 1 / (self.t_ssp / self.dt)

    def print_gait_parameter(self):
        print("zc :", self.zc)
        print("dt :", self.dt)
        print("t_preview :", self.t_preview)
        print("A_d :", self.A_d)
        print("B_d :", self.B_d)
        print("C_d :", self.C_d)
        print("Gi :", self.Gi)
        print("Gx :", self.Gx)
        print("Gd :", self.Gd)
        print("Preview Length :", self.preview_len)

    def rot_path(self, init_angle, target_angle, time, t):
        p0 = np.array([[0],[init_angle]])
        p1 = np.array([[0],[target_angle]])
        p2 = np.array([[time],[target_angle]])
        p3 = np.array([[time],[target_angle]])
        path = np.power((1-t), 3)*p0 + 3*np.power((1-t), 2)*t*p1 + 3*(1-t)*np.power(t, 2)*p2 + np.power(t, 3)*p3
        return path

    def swap_support_foot(self):
        if self.support_foot == 0:
            self.support_foot = 1
        else:
            self.support_foot = 0

    def swing_foot_path(self, str_pt, end_pt, swing_height, t):
        p0 = str_pt.copy()
        p1 = str_pt.copy()
        p1[2,0] = swing_height+(0.25*swing_height)
        p2 = end_pt.copy()
        p2[2,0] = swing_height+(0.25*swing_height)
        p3 = end_pt.copy()
        path = np.power((1-t), 3)*p0 + 3*np.power((1-t), 2)*t*p1 + 3*(1-t)*np.power(t, 2)*p2 + np.power(t, 3)*p3
        return path

    def get_foot_trajectory(self):
        # Mendapatkan posisi awal dan posisi akhir dari swing foot berupa position x, y, z
        # ini ditambahkan dengan inisial posisi dan orientasi dari swing foot
        if self.t == 0:
            if self.support_foot == 0:
                self.init_rfoot_pose[0,0] = self.cur_rfoot[0]
                self.init_rfoot_pose[1,0] = self.cur_rfoot[1]
                self.init_rfoot_pose[2,0] = 0
                self.init_rfoot_pose[3,0] = self.cur_rfoot[3]
                self.init_rfoot_pose[4,0] = self.cur_rfoot[4]
                self.init_rfoot_pose[5,0] = self.cur_rfoot[5]
                self.init_rfoot_pose[6,0] = self.cur_rfoot[6]
                # ambil vector posisi dari vector pose
                self.init_rfoot_position[0,0] = self.init_rfoot_pose[0,0]
                self.init_rfoot_position[1,0] = self.init_rfoot_pose[1,0]
                self.init_rfoot_position[2,0] = self.init_rfoot_pose[2,0]
                euler = euler_from_quaternion([self.init_rfoot_pose[3,0], self.init_rfoot_pose[4,0], self.init_rfoot_pose[5,0], self.init_rfoot_pose[6,0]])
                self.init_rfoot_orientation_yaw = euler[2] # diambil yaw saja

                self.target_rfoot_pose[0,0] = self.footstep[1][0]
                self.target_rfoot_pose[1,0] = self.footstep[1][1]
                self.target_rfoot_pose[2,0] = 0
                # disini ambil rotation dari alpha footstep di convert ke quartenion
                q = quaternion_from_euler(0, 0, self.footstep[1][2])
                self.target_rfoot_pose[3,0] = q[0]
                self.target_rfoot_pose[4,0] = q[1]
                self.target_rfoot_pose[5,0] = q[2]
                self.target_rfoot_pose[6,0] = q[3]
                # ambil vector posisi 
                self.target_rfoot_position[0,0] = self.target_rfoot_pose[0,0]
                self.target_rfoot_position[1,0] = self.target_rfoot_pose[1,0]
                self.target_rfoot_position[2,0] = self.target_rfoot_pose[2,0]
                euler = euler_from_quaternion([self.target_rfoot_pose[3,0], self.target_rfoot_pose[4,0], self.target_rfoot_pose[5,0], self.target_rfoot_pose[6,0]])
                self.target_rfoot_orientation_yaw = euler[2]
                # ini untuk mencari initial com yaw dan target com yaw
                euler = euler_from_quaternion([self.cur_lfoot[3], self.cur_lfoot[4], self.cur_lfoot[5], self.cur_lfoot[6]])
                support_foot_yaw = euler[2]
                self.init_com_yaw = (support_foot_yaw + self.init_rfoot_orientation_yaw) / 2
                self.target_com_yaw = (support_foot_yaw + self.target_rfoot_orientation_yaw) / 2
            else:
                self.init_lfoot_pose[0,0] = self.cur_lfoot[0]
                self.init_lfoot_pose[1,0] = self.cur_lfoot[1]
                self.init_lfoot_pose[2,0] = 0
                self.init_lfoot_pose[3,0] = self.cur_lfoot[3]
                self.init_lfoot_pose[4,0] = self.cur_lfoot[4]
                self.init_lfoot_pose[5,0] = self.cur_lfoot[5]
                self.init_lfoot_pose[6,0] = self.cur_lfoot[6]
                self.init_lfoot_position[0,0] = self.init_lfoot_pose[0,0]
                self.init_lfoot_position[1,0] = self.init_lfoot_pose[1,0]
                self.init_lfoot_position[2,0] = self.init_lfoot_pose[2,0]
                
                euler = euler_from_quaternion([self.init_lfoot_pose[3,0], self.init_lfoot_pose[4,0], self.init_lfoot_pose[5,0], self.init_lfoot_pose[6,0]])
                
                self.init_lfoot_orientation_yaw = euler[2]
                # disini ambil rotation dari alpha footstep di convert ke quartenion yaw
                self.target_lfoot_pose[0,0] = self.footstep[1][0]
                self.target_lfoot_pose[1,0] = self.footstep[1][1]
                self.target_lfoot_pose[2,0] = 0
                q = quaternion_from_euler(0, 0, self.footstep[1][2])
                self.target_lfoot_pose[3,0] = q[0]
                self.target_lfoot_pose[4,0] = q[1]
                self.target_lfoot_pose[5,0] = q[2]
                self.target_lfoot_pose[6,0] = q[3]
                self.target_lfoot_position[0,0] = self.target_lfoot_pose[0,0]
                self.target_lfoot_position[1,0] = self.target_lfoot_pose[1,0]
                self.target_lfoot_position[2,0] = self.target_lfoot_pose[2,0]
                euler = euler_from_quaternion([self.target_lfoot_pose[3,0], self.target_lfoot_pose[4,0], self.target_lfoot_pose[5,0], self.target_lfoot_pose[6,0]])
                self.target_lfoot_orientation_yaw = euler[2]
                # untuk mencari com traj
                euler = euler_from_quaternion([self.cur_rfoot[3], self.cur_rfoot[4], self.cur_rfoot[5], self.cur_rfoot[6]])
                support_foot_yaw = euler[2]
                self.init_com_yaw = (support_foot_yaw + self.init_lfoot_orientation_yaw) / 2
                self.target_com_yaw = (support_foot_yaw + self.target_lfoot_orientation_yaw) / 2

        # Generate foot trajectory untuk kaki kanan dan kaki kiri
        if self.t < (self.t_dsp/2.0) or self.t >= (self.t_dsp/2.0 + self.t_ssp):
            # print("DSP Phase")
            self.walking_phase = 0
            self.t_bez = 0
        else:
            # print("SSP Phase")
            self.walking_phase = 1
            if self.support_foot == 0:
                self.cur_lfoot[0] = self.footstep[0][0]
                self.cur_lfoot[1] = self.footstep[0][1]
                self.cur_lfoot[2] = 0
                # q = quaternion_from_axis_angle([0,0,1,self.footstep[0][2]])
                q = quaternion_from_euler(0,0,self.footstep[0][2])
                self.cur_lfoot[3] = q[0]
                self.cur_lfoot[4] = q[1]
                self.cur_lfoot[5] = q[2]
                self.cur_lfoot[6] = q[3]
                path = self.swing_foot_path(self.init_rfoot_position, self.target_rfoot_position, self.max_swing_height, self.t_bez)
                self.cur_rfoot[0] = path[0,0]
                self.cur_rfoot[1] = path[1,0]
                self.cur_rfoot[2] = path[2,0]
                # ini nanti ditambah rot path disini
                yaw_path = self.rot_path(self.init_rfoot_orientation_yaw, self.target_rfoot_orientation_yaw, self.t_ssp, self.t_bez)
                # q = quaternion_from_axis_angle([0,0,1,yaw_path[1,0]])
                q = quaternion_from_euler(0,0,yaw_path[1,0])
                self.cur_rfoot[3] = q[0]
                self.cur_rfoot[4] = q[1]
                self.cur_rfoot[5] = q[2]
                self.cur_rfoot[6] = q[3]
                # pr, pp, py = euler_from_quaternion([q[1], q[2], q[3], q[0]])
                # print("kaki kanan : ", np.degrees(pr), np.degrees(pp), np.degrees(py))
            else:
                self.cur_rfoot[0] = self.footstep[0][0]
                self.cur_rfoot[1] = self.footstep[0][1]
                self.cur_rfoot[2] = 0
                # q = quaternion_from_axis_angle([0,0,1,self.footstep[0][2]])
                q = quaternion_from_euler(0,0,self.footstep[0][2])
                self.cur_rfoot[3] = q[0]
                self.cur_rfoot[4] = q[1]
                self.cur_rfoot[5] = q[2]
                self.cur_rfoot[6] = q[3]
                path = self.swing_foot_path(self.init_lfoot_position, self.target_lfoot_position, self.max_swing_height, self.t_bez)
                self.cur_lfoot[0] = path[0,0]
                self.cur_lfoot[1] = path[1,0]
                self.cur_lfoot[2] = path[2,0]
                # q = quaternion_from_axis_angle([0,0,1,self.footstep[1][2]])
                yaw_path = self.rot_path(self.init_lfoot_orientation_yaw, self.target_lfoot_orientation_yaw, self.t_ssp, self.t_bez)
                # q = quaternion_from_axis_angle([0,0,1,yaw_path[1,0]])
                q = quaternion_from_euler(0,0, yaw_path[1,0])
                self.cur_lfoot[3] = q[0]
                self.cur_lfoot[4] = q[1]
                self.cur_lfoot[5] = q[2]
                self.cur_lfoot[6] = q[3]
                # pr, pp, py = euler_from_quaternion([q[1], q[2], q[3], q[0]])
                # print("kaki kiri : ", np.degrees(pr), np.degrees(pp), np.degrees(py))
            # update com traj
            # roll, pitch, yaw
            # roll = np.radians(0)
            # pitch = np.radians(0) # ini nanti diambil dari body tilt
            yaw_path = self.rot_path(self.init_com_yaw, self.target_com_yaw, self.t_ssp, self.t_bez)
            if len(self.left_foot) == self.preview_len:
                self.com_yaw.pop(0)
            self.com_yaw.append(yaw_path[1,0])
            # q = quaternion_from_euler(roll, pitch, yaw_path[1,0])
            # self.com[3] = q[0]
            # self.com[4] = q[1]
            # self.com[5] = q[2]
            # self.com[6] = q[3]
            # update timer bezier
            self.t_bez += self.dt_bez
        
        if len(self.left_foot) == self.preview_len:
            self.left_foot.pop(0)
            self.right_foot.pop(0)

        self.left_foot.append([self.cur_lfoot[0], self.cur_lfoot[1], self.cur_lfoot[2], self.cur_lfoot[3], self.cur_lfoot[4], self.cur_lfoot[5], self.cur_lfoot[6]])
        self.right_foot.append([self.cur_rfoot[0], self.cur_rfoot[1], self.cur_rfoot[2], self.cur_rfoot[3], self.cur_rfoot[4], self.cur_rfoot[5], self.cur_rfoot[6]])
    # Fungsi untuk mengenerate zmp trajectory
    def get_zmp_trajectory(self):
        if len(self.zmp_x) == self.preview_len: 
            self.zmp_x.pop(0)
            self.zmp_y.pop(0)
            self.zmp_a.pop(0)
        self.zmp_x.append(self.footstep[0][0])
        self.zmp_y.append(self.footstep[0][1])
        self.zmp_a.append(self.footstep[0][2])
        
    def add_new_footstep(self):
        if self.cnt % int(self.t_step / self.dt) == 0:
            # print("New Step")
            self.footstep.pop(0)
            # dx = footstep sebelumnya ditambah dengan 
            
            if self.support_foot == 0: # kiri
                self.sx = self.cmd_x
                self.sy = -2*self.hip_offset + self.cmd_y
                # print("0 sx sy", sx, sy)
                self.sa += self.cmd_a
                dx = self.footstep[-1][0] + np.cos(self.sa) * self.sx + (-np.sin(self.sa) * self.sy)
                dy = self.footstep[-1][1] + np.sin(self.sa) * self.sx + np.cos(self.sa) * self.sy
                self.footstep.append([dx, dy, self.sa])
            else:
                self.sx = self.cmd_x 
                self.sy = 2*self.hip_offset + self.cmd_y
                # print("1 sx sy", sx, sy)
                self.sa += self.cmd_a
                dx = self.footstep[-1][0] + np.cos(self.sa) * self.sx + (-np.sin(self.sa) * self.sy)
                dy = self.footstep[-1][1] + np.sin(self.sa) * self.sx + np.cos(self.sa) * self.sy
                self.footstep.append([dx, dy, self.sa])
            self.swap_support_foot()      
        self.cnt += 1

    # Fungsi untuk mengenerate com trajectory
    def get_preview_control(self):
        y_x = self.C_d.dot(self.x_x).item()
        y_y = self.C_d.dot(self.x_y).item()
        
        e_x = self.zmp_x[0] - y_x
        e_y = self.zmp_y[0] - y_y

        preview_x = 0
        preview_y = 0

        for j in range(0, self.preview_len):
            preview_x += self.Gd[0, j] * self.zmp_x[j]
            preview_y += self.Gd[0, j] * self.zmp_y[j]

        u_x = -self.Gi * e_x - self.Gx.dot(self.x_x) - preview_x
        u_x = u_x.item()
        u_y = -self.Gi * e_y - self.Gx.dot(self.x_y) - preview_y
        u_y = u_y.item()
        
        self.x_x = self.A_d.dot(self.x_x) + self.B_d * u_x 
        self.x_y = self.A_d.dot(self.x_y) + self.B_d * u_y

        # ini com yang baru
        self.com[0] = self.x_x[0,0] 
        self.com[1] = self.x_y[0,0]
        self.com[2] = self.zc 
        # roll, pitch, yaw
        roll = np.radians(0)
        pitch = np.radians(0) # ini nanti diambil dari body tilt
        # yaw = self.zmp_a[0]
        q = quaternion_from_euler(roll, pitch, self.com_yaw[0])
        
        self.com[3] = q[0]
        self.com[4] = q[1]
        self.com[5] = q[2]
        self.com[6] = q[3]

    def create_tf_matrix(self, list_xyz_qxyzw):
        T_mat = np.eye(4)
        T_mat[0,3] = list_xyz_qxyzw[0]
        T_mat[1,3] = list_xyz_qxyzw[1]
        T_mat[2,3] = list_xyz_qxyzw[2]
        R_mat = matrix_from_quaternion([list_xyz_qxyzw[6], list_xyz_qxyzw[3], list_xyz_qxyzw[4], list_xyz_qxyzw[5]])
        T_mat[:3,:3] = R_mat
        return T_mat

    # Fungsi ini digunakan untuk mendapatkan pose kaki kanan dan kaki kiri relative terhadap CoM
    def get_foot_pose(self):
        world_to_com = self.create_tf_matrix(self.com)
        world_to_lfoot = self.create_tf_matrix(self.left_foot[0])
        world_to_rfoot = self.create_tf_matrix(self.right_foot[0])
        world_to_com_inv = np.linalg.pinv(world_to_com)
        com_to_lfoot = world_to_com_inv.dot(world_to_lfoot)
        com_to_rfoot = world_to_com_inv.dot(world_to_rfoot)
        q_lfoot = quaternion_from_matrix(com_to_lfoot[:3,:3])
        q_rfoot = quaternion_from_matrix(com_to_rfoot[:3,:3])
        self.left_foot_pose = [com_to_lfoot[0,3], com_to_lfoot[1,3], com_to_lfoot[2,3], q_lfoot[1], q_lfoot[2], q_lfoot[3], q_lfoot[0]]
        self.right_foot_pose = [com_to_rfoot[0,3], com_to_rfoot[1,3], com_to_rfoot[2,3], q_rfoot[1], q_rfoot[2], q_rfoot[3], q_rfoot[0]]
    
    # Fungsi untuk mendapatkan com dan foot trajectory dalam bentuk 3d pose
    def get_walking_pattern(self):
        self.get_foot_trajectory()
        self.get_zmp_trajectory()

        # Cek apakah future reference sudah cukup
        if len(self.zmp_x) == self.preview_len:
            self.pattern_ready = True
            self.get_preview_control()
            self.get_foot_pose()

        self.t += self.dt 
        if self.t > self.t_step:
            self.t = 0

        self.add_new_footstep()

    def initialize(self):
        # Initialize gait controller
        # gait_param_file = '../data/wpg_parameter.mat'
        self.get_gait_parameter()
        self.print_gait_parameter()
        # offset parameter
        # self.support_x = rospy.get_param("/gait_param/support_x")
        self.support_x = 0.01
        
    def end(self):
        pass

    def print_pose(self, name, pose, rpy_mode = True):
        if rpy_mode:
            euler = euler_from_quaternion([pose[3], pose[4], pose[5], pose[6]])
            print(name + " xyz : " + "{0:.3f}".format(pose[0]) +", "+ "{0:.3f}".format(pose[1]) +", "+ "{0:.3f}".format(pose[2]) + \
            " rpy : " + "{0:.3f}".format(euler[0]) +", "+ "{0:.3f}".format(euler[1]) +", "+ "{0:.3f}".format(euler[2]))
        else:
            print(name + " xyz : " + "{0:.3f}".format(pose[0]) +", "+ "{0:.3f}".format(pose[1]) +", "+ "{0:.3f}".format(pose[2]) + \
            " xyzw : " + "{0:.3f}".format(pose[3]) +", "+ "{0:.3f}".format(pose[4]) +", "+ "{0:.3f}".format(pose[5]) +", "+ "{0:.3f}".format(pose[6]))

    def run(self):
        print("===========================")
        print("Barelang Gait Controller   ")
        print("===========================")
        self.initialize()
        print("Start Stepping")
        print("LIPM :", self.zc)
        t_sim = 4
        t = 0
        list_com_x = []
        list_com_y = []
        list_com_z = []
        list_zmp_x = []
        list_zmp_y = []
        list_zmp_z = []
        list_r_foot_x = []
        list_r_foot_y = []
        list_r_foot_z = []
        list_l_foot_x = []
        list_l_foot_y = []
        list_l_foot_z = []
        com_trajectory = []
        lfoot_trajectory = []
        rfoot_trajectory = []
        while t < t_sim:
            self.get_walking_pattern()
            if self.pattern_ready:
                # self.print_pose("com", self.com, rpy_mode=False)
                # self.print_pose("left foot", self.left_foot[0], rpy_mode=False)
                # self.print_pose("left foot pose", self.left_foot_pose, rpy_mode=False)
                # self.print_pose("right foot", self.right_foot[0], rpy_mode=False)
                # self.print_pose("right foot pose", self.right_foot_pose, rpy_mode=False)
                # print("=============================================")
                list_com_x.append(self.com[0])
                list_com_y.append(self.com[1])
                list_com_z.append(self.com[2])
                com_trajectory.append([self.com[0], self.com[1], self.com[2], self.com[6], self.com[3], self.com[4], self.com[5]])
                list_zmp_x.append(self.zmp_x[0])
                list_zmp_y.append(self.zmp_y[0])
                list_zmp_z.append(0)
                list_r_foot_x.append(self.right_foot[0][0])
                list_r_foot_y.append(self.right_foot[0][1])
                list_r_foot_z.append(self.right_foot[0][2])
                rfoot_trajectory.append([self.right_foot[0][0], self.right_foot[0][1], self.right_foot[0][2], self.right_foot[0][6], self.right_foot[0][3], self.right_foot[0][4], self.right_foot[0][5]])
                list_l_foot_x.append(self.left_foot[0][0])
                list_l_foot_y.append(self.left_foot[0][1])
                list_l_foot_z.append(self.left_foot[0][2])
                lfoot_trajectory.append([self.left_foot[0][0], self.left_foot[0][1], self.left_foot[0][2], self.left_foot[0][6], self.left_foot[0][3], self.left_foot[0][4], self.left_foot[0][5]])
            t += self.dt

        record_trajectory = True

        if record_trajectory:
            f = open('../data/trajectory.txt', 'w')
            for i in range(len(rfoot_trajectory)):
                f.write(str(rfoot_trajectory[i]))
            f.close()
        fig = plt.figure(1)
        ax = fig.add_subplot(111, projection='3d')
        com_trajectory = np.array(com_trajectory)
        rfoot_trajectory = np.array(rfoot_trajectory)
        lfoot_trajectory = np.array(lfoot_trajectory)
        # print("foot shape:", rfoot_trajectory.shape[1])
        plot_trajectory(ax=ax, P=com_trajectory, n_frames=com_trajectory.shape[0], s=0.02, show_direction=True)
        plot_trajectory(ax=ax, P=rfoot_trajectory, n_frames=rfoot_trajectory.shape[0], s=0.02, show_direction=True)
        plot_trajectory(ax=ax, P=lfoot_trajectory, n_frames=lfoot_trajectory.shape[0], s=0.02, show_direction=True)
        # ax.plot(list_com_x, list_com_y, list_com_z, 'o')
        # ax.plot(list_zmp_x, list_zmp_y, list_zmp_z)
        # ax.plot(list_r_foot_x, list_r_foot_y, list_r_foot_z, 'o')
        # ax.plot(list_l_foot_x, list_l_foot_y, list_l_foot_z, 'o')
        ax.set_xlim3d(-0.1, 0.5)
        ax.set_ylim3d(-0.1, 0.5)
        ax.set_zlim3d(0, 0.3)
        plt.show()
        self.end()

def quaternion_matrix(quaternion): 
    q = np.array(quaternion[:4], dtype=np.float64, copy=True) 
    nq = np.dot(q, q) 
    if nq < _EPS: 
         return np.identity(4) 
    q *= math.sqrt(2.0 / nq) 
    q = np.outer(q, q) 
    return np.array(( 
          (1.0-q[1, 1]-q[2, 2],     q[0, 1]-q[2, 3],     q[0, 2]+q[1, 3], 0.0), 
          (    q[0, 1]+q[2, 3], 1.0-q[0, 0]-q[2, 2],     q[1, 2]-q[0, 3], 0.0), 
          (    q[0, 2]-q[1, 3],     q[1, 2]+q[0, 3], 1.0-q[0, 0]-q[1, 1], 0.0), 
          (                0.0,                 0.0,                 0.0, 1.0) 
         ), dtype=np.float64)

def euler_from_quaternion(quaternion, axes='sxyz'): 
    return euler_from_matrix(quaternion_matrix(quaternion), axes) 
    
def euler_from_matrix(matrix, axes='sxyz'): 

    try: 
        firstaxis, parity, repetition, frame = _AXES2TUPLE[axes.lower()] 
    except (AttributeError, KeyError): 
        _ = _TUPLE2AXES[axes] 
        firstaxis, parity, repetition, frame = axes 
   
    i = firstaxis 
    j = _NEXT_AXIS[i+parity] 
    k = _NEXT_AXIS[i-parity+1] 
   
    M = np.array(matrix, dtype=np.float64, copy=False)[:3, :3] 
    if repetition: 
        sy = math.sqrt(M[i, j]*M[i, j] + M[i, k]*M[i, k]) 
        if sy > _EPS: 
            ax = math.atan2( M[i, j],  M[i, k]) 
            ay = math.atan2( sy,       M[i, i]) 
            az = math.atan2( M[j, i], -M[k, i]) 
        else: 
            ax = math.atan2(-M[j, k],  M[j, j]) 
            ay = math.atan2( sy,       M[i, i]) 
            az = 0.0 
    else: 
        cy = math.sqrt(M[i, i]*M[i, i] + M[j, i]*M[j, i]) 
        if cy > _EPS: 
            ax = math.atan2( M[k, j],  M[k, k]) 
            ay = math.atan2(-M[k, i],  cy) 
            az = math.atan2( M[j, i],  M[i, i]) 
        else: 
            ax = math.atan2(-M[j, k],  M[j, j]) 
            ay = math.atan2(-M[k, i],  cy) 
            az = 0.0 
   
    if parity: 
        ax, ay, az = -ax, -ay, -az 
    if frame: 
        ax, az = az, ax 
    return ax, ay, az 

def quaternion_from_euler(ai, aj, ak, axes='sxyz'): 
    try: 
        firstaxis, parity, repetition, frame = _AXES2TUPLE[axes.lower()] 
    except (AttributeError, KeyError): 
        _ = _TUPLE2AXES[axes] 
        firstaxis, parity, repetition, frame = axes 
   
    i = firstaxis 
    j = _NEXT_AXIS[i+parity] 
    k = _NEXT_AXIS[i-parity+1] 
   
    if frame: 
        ai, ak = ak, ai 
    if parity: 
        aj = -aj 
   
    ai /= 2.0 
    aj /= 2.0 
    ak /= 2.0 
    ci = math.cos(ai) 
    si = math.sin(ai) 
    cj = math.cos(aj) 
    sj = math.sin(aj) 
    ck = math.cos(ak) 
    sk = math.sin(ak) 
    cc = ci*ck 
    cs = ci*sk 
    sc = si*ck 
    ss = si*sk 
   
    quaternion = np.empty((4, ), dtype=np.float64) 
    if repetition: 
        quaternion[i] = cj*(cs + sc) 
        quaternion[j] = sj*(cc + ss) 
        quaternion[k] = sj*(cs - sc) 
        quaternion[3] = cj*(cc - ss) 
    else: 
        quaternion[i] = cj*sc - sj*cs 
        quaternion[j] = cj*ss + sj*cc 
        quaternion[k] = cj*cs - sj*sc 
        quaternion[3] = cj*cc + sj*ss 
    if parity: 
        quaternion[j] *= -1 
    return quaternion   
    
def main():
    gc = GaitController()
    gc.run()

if __name__ == "__main__":
    main()