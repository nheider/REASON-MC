'''
TO DO: 
    - program implement up/down -> change max steering angle dependent on implement position 
    - enable backwards driving 
    - enable obstacle generation before and during episode 
    - enable field shape change during epsiode 
    - parallelize 
'''

import numpy as np 
import matplotlib.pyplot as plt
plt.ion() 
import cv2 
import math 
import pyclipper
from scipy.spatial import ConvexHull
from matplotlib.patches import Polygon
from matplotlib.animation import FuncAnimation
import gymnasium as gym 
from gymnasium import spaces, envs


class Env: # Contains all the logic of the CPP Environment  
    def __init__(self, max_size=200, num_points=8, vehicle_width=10, sub_steps=10):

        # Field Variables 
        self.max_size = max_size           # Largest possible x and y-coords of the field
        self.num_points = num_points       # Number of random points for the convex hull field generator, higher number = higher mean number of fiel vertices 
        self.bounding_box = []             # Bounding Box of the generated field 
        self.cover_polygon = []            # A polygon that records the intersection between the Vehicle Path and the Field 
        self.cover_matrix = None           # A Matrix that is 1 where the vehicle covered the field and 0 where it didnt pass. 
        self.start_point = None            # the sarting point of the agent 
        self.heading = None                # Angle the agent is currently heading towards 
        self.new_polygon = None            # Used to update the visit count matrix 
        self.outside = False               # True if agent has exited the field 
        self.old_visits = None             # Matrix that records the previously visited cells, before the step
        self.completed = None 
        self.new_area = None 
        self.overlap_area = None 
        
        self.create_field()
        self.create_field_matrix()
        self.random_point_on_polygon_perimeter()

        self.visit_matrix = np.zeros_like(self.matrix, dtype=np.int32) # Records the number of times each cell gets visited, used for visualization        

        # Path Variables 
        self.width = vehicle_width         # The width of the vehicles covered path 
        self.sub_steps = sub_steps         # Number of picewise linear spline points of the path segments, higher := better resolution 
        self.path = [self.start_point]  
        self.left_edge = []
        self.right_edge = []

    def create_field(self):
        points = np.random.randint(0, self.max_size, size=(self.num_points, 2))
        hull = ConvexHull(points)
        field_points = points[hull.vertices].tolist()
        field_points.append(field_points[0]) # Ensures Polygon is closed

        x_coordinates, y_coordinates = zip(*field_points)

        self.bounding_box = [min(x_coordinates), max(x_coordinates), min(y_coordinates), max(y_coordinates)]
        self.polygon = field_points

    def create_field_matrix(self):
        field_vertices = np.array([self.polygon], dtype=np.int32)
        matrix = np.full((self.max_size, self.max_size), 99, dtype=np.uint8) # Generate a Matrix the size of the biggest possible field 
        field_matrix = cv2.fillPoly(matrix, field_vertices, 0)               # Fill cells that are inside the field polygon with 0 
        self.matrix = field_matrix                                           # Matrix with 99 := Outside, 0 := Field 

    def size(self):
        mask = self.matrix != 99
        count_of_elements = np.sum(mask)
        return count_of_elements
        
    def create_cover_matrix(self):
        temp_mat = np.zeros_like(self.matrix)
        cover_polygon_vertices = np.array([self.cover_polygon], dtype=np.int32)
        self.cover_matrix = cv2.fillPoly(temp_mat, cover_polygon_vertices, 1)  

    def update_matrix(self):
        self.matrix[(self.matrix != 99) & (self.cover_matrix == 1)] = 1

    def update_visit_counts(self):
        self.visit_matrix[(self.matrix != 99) & (self.cover_matrix == 1) & (self.old_visits != 1)] += 1     

    def get_stats(self):
        field_mask = self.matrix != 99
        visits = self.visit_matrix[field_mask]
        return {
            'max_visits': np.max(visits),
            'min_visits': np.min(visits),
            'avg_visits': np.mean(visits),
            'total_visits': np.sum(visits),
            'total_unique_visits': np.count_nonzero(visits),
            'unvisited_cells': np.sum(visits == 0)
        }
    
    def check_if_outside(self):
        if ((self.matrix == 99) & (self.cover_matrix == 1)).any(): 
            self.outside = True
            return 
   
    def random_point_on_polygon_perimeter(self): # To determin the start point and heading of the agent 
        perimeter = 0
        for i in range(len(self.polygon)):
            x1, y1 = self.polygon[i]
            x2, y2 = self.polygon[(i + 1) % len(self.polygon)]
            perimeter += np.sqrt((x2 - x1) ** 2 + (y2 - y1) ** 2)
 
        random_distance = np.random.uniform(0, perimeter)
        cumulative_distance = 0

        for i in range(len(self.polygon)-1):
            x1, y1 = self.polygon[i]
            x2, y2 = self.polygon[(i + 1) % len(self.polygon)]
            segment_length = np.sqrt((x2 - x1) ** 2 + (y2 - y1) ** 2)
        
            if cumulative_distance + segment_length >= random_distance:
                segment_fraction = (random_distance - cumulative_distance) / segment_length
                px = x1 + segment_fraction * (x2 - x1)
                py = y1 + segment_fraction * (y2 - y1)

                # Calculate the heading perpendicular to the edge
                edge_angle_radians = math.atan2(y2 - y1, x2 - x1)
                perpendicular_heading_radians = edge_angle_radians + math.radians(90)
                perpendicular_heading_degrees = math.degrees(perpendicular_heading_radians)

                self.start_point = [px, py]
                self.heading = perpendicular_heading_degrees % 360
                return 
            cumulative_distance += segment_length


    def next_point_in_path(self, spline_len, spline_angle_degrees, start = False):
        x1, y1 = self.path[-1]
        total_angle_radians = math.radians(self.heading + spline_angle_degrees)

        # Calculate the endpoint
        x2 = x1 + spline_len * math.cos(total_angle_radians)
        y2 = y1 + spline_len * math.sin(total_angle_radians)

        # Calculate offsets for width
        offset_top_dx, offset_top_dy = self.calculate_offsets(total_angle_radians, self.width)
        offset_bottom_dx, offset_bottom_dy = self.calculate_offsets(total_angle_radians, -self.width)

        # Top and bottom endpoints
        x2_top = x2 + offset_top_dx
        y2_top = y2 + offset_top_dy
        x2_bot = x2 + offset_bottom_dx
        y2_bot = y2 + offset_bottom_dy

        # Edge case width for the first ever segment, that gets placed randomly # To do: rewrite function so this is not needed 
        if start: 
            initial_angle_radians = math.radians(self.heading)
            initial_offset_top_dx, initial_offset_top_dy = self.calculate_offsets(initial_angle_radians, self.width)
            initial_offset_bottom_dx, initial_offset_bottom_dy = self.calculate_offsets(initial_angle_radians, -self.width)
            
            x1_top = x1 + initial_offset_top_dx
            y1_top = y1 + initial_offset_top_dy
            x1_bot = x1 + initial_offset_bottom_dx
            y1_bot = y1 + initial_offset_bottom_dy

        else: 
            x1_top = y1_top = x1_bot = y1_bot = None 

        return [x2, y2], [x2_top, y2_top], [x2_bot, y2_bot], [x1_top, y1_top], [x1_bot, y1_bot]

    def calculate_offsets(self, angle_radians, width):
        offset_angle = angle_radians - math.radians(90)
        offset_dx = 0.5 * width * math.cos(offset_angle)
        offset_dy = 0.5 * width * math.sin(offset_angle)
        return offset_dx, offset_dy

    def steering_to_curve(self, distance, steering_angle):
        if steering_angle < -90 or steering_angle > 90:
            raise ValueError("only works with steering angles up to +/- 90 degrees")

        spline_len = distance / self.sub_steps
        spline_angle = steering_angle / self.sub_steps

        # new_path and new_polygon get used to update the cell visit count
        new_polygon = []
        new_left_edge = []
        new_right_edge = []

        for i in range(self.sub_steps):
            if i == 0:
                mid, top, bot, initial_top, initial_bot = self.next_point_in_path(spline_len, spline_angle, start=True)
                self.left_edge.append(initial_top)
                self.right_edge.append(initial_bot)

                new_left_edge.append(initial_top)
                new_right_edge.append(initial_bot)

            else:
                mid, top, bot, _, _ = self.next_point_in_path(spline_len, spline_angle)

            self.heading += spline_angle

            self.path.extend([mid])
            self.left_edge.extend([top])
            self.right_edge.extend([bot])

            new_left_edge.extend([top])
            new_right_edge.extend([bot])


        self.cover_polygon = new_left_edge + list(reversed(new_right_edge))
        if self.cover_polygon[0] != self.cover_polygon[-1]:
            self.cover_polygon.append(self.cover_polygon[0])

        # Create the path polygon
        self.path_polygon = self.left_edge + list(reversed(self.right_edge))
        if self.path_polygon[0] != self.path_polygon[-1]:
            self.path_polygon.append(self.path_polygon[0])
     
    def extend_path(self, distance, steering_angle):
       self.steering_to_curve(distance=distance, steering_angle=steering_angle)

    def position(self):
        self.position = self.path[-1]

    def calculate_new_area(self):
        prev_values, prev_counts = np.unique(self.old_visits, return_counts=True)

        if len(prev_counts) == 2 and not (self.matrix == 1).any(): # Edge case for the first ever run 
            prev_counts = [prev_counts[0], 0, prev_counts[1]]

        current_counts = np.unique(self.matrix, return_counts=True)[1][1]
        self.new_area = current_counts - prev_counts[1]

    def calculate_overlap_area(self): 
        changed_cells = np.sum(self.matrix != self.old_visits) - self.new_area 
        self.overlap_area = changed_cells
    
    def check_if_completed(self):
        self.completed = np.all((self.matrix == 99) | (self.matrix == 1))

    def step(self, distance, steering_angle, visualize=False): 
        self.old_visits = self.matrix
        self.extend_path(distance=distance, steering_angle=steering_angle)
        self.create_cover_matrix()
        self.update_matrix()
        self.update_visit_counts()
        self.check_if_completed()
        self.check_if_outside()
        self.calculate_new_area()
        self.calculate_overlap_area()
        
        if visualize: 
            self.visualize(show_visits=True)

    def visualize(self, show_visits=False):
        fig, ax = plt.subplots(figsize=(10, 10))
    
       # if show_visits:
            # Create a masked array for visit counts
        #    masked_visits = np.ma.masked_where(self.matrix == 99, self.visit_matrix)
        #    im = ax.imshow(masked_visits, cmap='viridis', interpolation='nearest')
        #    plt.colorbar(im, ax=ax, label='Visit Count')
            #plt.imshow(self.visit_matrix)
            #values, counts = np.unique(self.visit_matrix, return_counts=True)
            #obs = zip(values, counts)
            #lst = list(zip(*obs))
            #print(lst)
        #    plt.show()
      
        # Plot the field
        field_polygon = Polygon(self.polygon, facecolor='lightgreen', edgecolor='green', alpha=0.5)
        ax.add_patch(field_polygon)
    
     # Plot the path if provided
        if self.path:
            path_x, path_y = zip(*self.path)
            ax.plot(path_x, path_y, 'r-', linewidth=2, label='Path')
    
        # Plot the path polygon if provided
        if self.path_polygon:
            path_poly_patch = Polygon(self.path_polygon, facecolor='red', edgecolor='red', alpha=0.3)
            ax.add_patch(path_poly_patch)
    
        # Set axis limits
        ax.set_xlim(self.bounding_box[0], self.bounding_box[1])
        ax.set_ylim(self.bounding_box[2], self.bounding_box[3])
        ax.set_aspect('equal', 'box')

        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)
        ax.spines["left"].set_visible(False)
        ax.spines["bottom"].set_visible(False)
    
        # Set labels and title
        ax.set_xlabel('X')
        ax.set_ylabel('Y')
        #ax.set_title('Field Visualization')
    
        # Add legend
        ax.legend()
    
        # Show the plot
        plt.draw()
        plt.pause(0.1)  # Pause for 1 second
        plt.close(fig)

# Custom environment
class FieldEnv(gym.Env):
    def __init__(self):
        super(FieldEnv, self).__init__()
        
        # Action space: steering angle and distance
        self.action_space = spaces.Box(
            low=np.array([-60.0, 10.0]),
            high=np.array([60.0, 100.0]),
            shape=(2,),
            dtype=np.float32
        )
        
        # Observation space: 1000x1000 matrix
        self.observation_space = spaces.Box(
            low=np.array([0] * 1000000 + [0, 0] + [-360]),
            high=np.array([99] * 1000000 + [1000.0, 1000.0] + [360]),
            shape=(1000003,),
            dtype=np.float32
        )
    
    def reset(self, seed=None, options=None):
        # Reset the environment and return initial observation
        self.env = Env(max_size=1000, num_points=8, vehicle_width=10, sub_steps=10)

        observation = np.concatenate([
            self.env.matrix.flatten(),
            np.array(self.env.path[-1], dtype=np.float32),
            np.array([self.env.heading], dtype=np.float32)
        ])

        info = {} # To do add some info 

        return (observation, info)
    
    
    def step(self, action):
        terminated = False
        truncated = False
        
        # Implement environment dynamics
        steering_angle = np.clip(action[0], -60, 60) 
        distance = np.clip(action[1], 10, 100) # To do: fix this 
        #finished = action[2] > 0.5  # Convert to boolean
        #print("steer", steering_angle, "dist", distance, "done", finished, "heading", self.env.heading)
        
        # Example: update state based on action
        self.env.step(distance = distance, steering_angle=steering_angle, visualize=False)

        observation = np.concatenate([
            self.env.matrix.flatten(),
            np.array(self.env.path[-1], dtype=np.float32),
            np.array([self.env.heading], dtype=np.float32)
        ])
    
        # Reward calculation
        alpha = 10  # Reward for new area covered
        beta = 5    # Penalty for overlap area
        gamma = 0.1 # Small time step penalty
        delta = 1000 # Large reward for completing the task
    
    # Reward components
        reward = (alpha * self.env.new_area) - (beta * self.env.overlap_area) - gamma

        print(self.env.new_area)
        print(self.env.overlap_area)
        print(reward)
    
    # If task is completed, give a large bonus
        if self.env.completed:
            reward += delta
    
    # Check for boundary violations or other termination conditions (not shown)
        if self.env.outside: 
            terminated = 1
    
    # Visualization (if requested)
    #    if visualize:
    #        self.visualize(show_visits=True)
    
    # Return the reward, the current state, and other information typically needed by RL algorithms
        return observation, reward, terminated, truncated, {}   # Simplified return statement

