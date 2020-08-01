from abc import ABC, abstractmethod
from threading import Lock, Thread
from queue import Queue, LifoQueue, Empty, Full
from time import time
from overcooked_ai_py.mdp.overcooked_mdp import OvercookedGridworld
from overcooked_ai_py.mdp.overcooked_env import OvercookedEnv
from overcooked_ai_py.mdp.actions import Action, Direction
import random, os, pickle

# TODO: Make base OvercookedGame and PsiturkOvercookedGame separate classes

# Relative path to where all static pre-trained agents are stored on server
AGENT_DIR = os.path.join(os.curdir, 'static', 'assets', 'agents')

class Game(ABC):

    """
    Class representing a game object. Coordinates the simultaneous actions of arbitrary
    number of players. Override this base class in order to use. 

    Players can post actions to a `pending_actions` queue, and driver code can call `tick` to apply these actions.


    It should be noted that most operations in this class are not on their own thread safe. Thus, client code should
    acquire `self.lock` before making any modifications to the instance. 

    One important exception to the above rule is `enqueue_actions` which is thread safe out of the box
    """

    # Possible TODO: create a static list of IDs used by the class so far to verify id uniqueness
    # This would need to be serialized, however, which might cause too great a performance hit to 
    # be worth it

    EMPTY = 'EMPTY'
    
    class Status:
        DONE = 'done'
        ACTIVE = 'active'
        RESET = 'reset'
        INACTIVE = 'inactive'

    def __init__(self, *args, **kwargs):
        """
        players (list): List of IDs of players currently in the game
        id (int):   Unique identifier for this game
        pending_actions List[(Queue)]: Buffer of (player_id, action) pairs have submitted that haven't been commited yet
        lock (Lock):    Used to serialize updates to the game state
        is_active(bool): Whether the game is currently being played or not
        """
        self.players = []
        self.pending_actions = []
        self.id = kwargs.get('id', id(self))
        self.lock = Lock()
        self._is_active = False

    @abstractmethod
    def is_full(self):
        """
        Returns whether there is room for additional players to join or not
        """
        pass

    @abstractmethod
    def apply_action(self, player_idx, action):
        """
        Updates the game state by applying a single (player_idx, action) tuple. Subclasses should try to override this method
        if possible
        """
        pass


    @abstractmethod
    def is_finished(self):
        """
        Returns whether the game has concluded or not
        """
        pass

    def is_ready(self):
        """
        Returns whether the game can be started. Defaults to having enough players
        """
        return self.is_full()

    @property
    def is_active(self):
        """
        Whether the game is currently being played
        """
        return self._is_active

    @property
    def reset_timeout(self):
        """
        Number of milliseconds to pause game on reset
        """
        return 3000

    def apply_actions(self):
        """
        Updates the game state by applying each of the pending actions in the buffer. Is called by the tick method. Subclasses
        should override this method if joint actions are necessary. If actions can be serialized, overriding `apply_action` is 
        preferred
        """
        for i in range(len(self.players)):
            try:
                while True:
                    action = self.pending_actions[i].get(block=False)
                    self.apply_action(i, action)
            except Empty:
                pass

    def activate(self):
        """
        Activates the game to let server know real-time updates should start. Provides little functionality but useful as
        a check for debugging
        """
        self._is_active = True

    def deactivate(self):
        """
        Deactives the game such that subsequent calls to `tick` will be no-ops. Used to handle case where game ends but 
        there is still a buffer of client pings to handle
        """
        self._is_active = False

    def reset(self):
        """
        Restarts the game while keeping all active players by resetting game stats and temporarily disabling `tick`
        """
        if not self.is_active:
            raise ValueError("Inactive Games cannot be reset")
        if self.is_finished():
            return self.Status.DONE
        self.deactivate()
        self.activate()
        return self.Status.RESET

    def needs_reset(self):
        """
        Returns whether the game should be reset on the next call to `tick`
        """
        return False


    def tick(self):
        """
        Updates the game state by applying each of the pending actions. This is done so that players cannot directly modify
        the game state, offering an additional level of safety and thread security. 

        One can think of "enqueue_action" like calling "git add" and "tick" like calling "git commit"

        Subclasses should try to override `apply_actions` if possible. Only override this method if necessary
        """ 
        if not self.is_active:
            return self.Status.INACTIVE
        if self.needs_reset():
            self.reset()
            return self.Status.RESET

        self.apply_actions()
        return self.Status.DONE if self.is_finished() else self.Status.ACTIVE
    
    def enqueue_action(self, player_id, action):
        """
        Add (player_id, action) pair to the pending action queue, without modifying underlying game state

        Note: This function IS thread safe
        """
        if not self.is_active:
            # Could run into issues with is_active not being thread safe
            return
        if player_id not in self.players:
            raise ValueError("Invalid player ID")
        try:
            player_idx = self.players.index(player_id)
            self.pending_actions[player_idx].put(action)
        except Full:
            pass

    def get_state(self):
        """
        Return a JSON compatible serialized state of the game. Note that this should be as minimalistic as possible
        as the size of the game state will be the most important factor in game performance. This is sent to the client
        every frame update.
        """
        return { "players" : self.players }

    def to_json(self):
        """
        Return a JSON compatible serialized state of the game. Contains all information about the game, does not need to
        be minimalistic. This is sent to the client only once, upon game creation
        """
        return self.get_state()

    def is_empty(self):
        """
        Return whether it is safe to garbage collect this game instance
        """
        return not len(self.players)

    def add_player(self, player_id, idx=None, buff_size=-1):
        """
        Add player_id to the game
        """
        if self.is_full():
            raise ValueError("Cannot add players to full game")
        if self.is_active:
            raise ValueError("Cannot add players to active games")
        if not idx and self.EMPTY in self.players:
            idx = self.players.index(self.EMPTY)
        elif not idx:
            idx = len(self.players)
        
        padding = max(0, idx - len(self.players) + 1)
        for _ in range(padding):
            self.players.append(self.EMPTY)
            self.pending_actions.append(self.EMPTY)
        
        self.players[idx] = player_id
        self.pending_actions[idx] = Queue(maxsize=buff_size)

    def remove_player(self, player_id):
        """
        Remove player_id from the game
        """
        try:
            idx = self.players.index(player_id)
            self.players[idx] = self.EMPTY
            self.pending_actions[idx] = self.EMPTY
        except ValueError:
            return False
        else:
            return True

    def clear_pending_actions(self):
        """
        Remove all queued actions for all players
        """
        for i, player in enumerate(self.players):
            if player != self.EMPTY:
                queue = self.pending_actions[i]
                queue.queue.clear()

    @property
    def num_players(self):
        return len([player for player in self.players if player != self.EMPTY])

    def get_data(self):
        """
        Returns any accumulated data about the game. Used for psiturk data collection
        """
        return {}
        


class DummyGame(Game):

    """
    Standin class used to test basic server logic
    """

    def __init__(self, **kwargs):
        super(DummyGame, self).__init__(**kwargs)
        self.counter = 0

    def is_full(self):
        return self.num_players == 2

    def apply_action(self, idx, action):
        pass

    def apply_actions(self):
        self.counter += 1

    def is_finished(self):
        return self.counter >= 100

    def get_state(self):
        state = super(DummyGame, self).get_state()
        state['count'] = self.counter
        return state


class DummyInteractiveGame(Game):

    """
    Standing class used to test interactive components of the server logic
    """

    def __init__(self, **kwargs):
        super(DummyInteractiveGame, self).__init__(**kwargs)
        self.max_players = int(kwargs.get('playerZero', 'human') == 'human') + int(kwargs.get('playerOne', 'human') == 'human')
        self.max_count = kwargs.get('max_count', 30)
        self.counter = 0
        self.counts = [0] * self.max_players

    def is_full(self):
        return self.num_players == self.max_players

    def is_finished(self):
        return max(self.counts) >= self.max_count

    def apply_action(self, player_idx, action):
        if action.upper() == 'UP':
            self.counts[player_idx] += 1
        if action.upper() == 'DOWN':
            self.counts[player_idx] -= 1

    def apply_actions(self):
        super(DummyInteractiveGame, self).apply_actions()
        self.counter += 1

    def get_state(self):
        state = super(DummyInteractiveGame, self).get_state()
        state['count'] = self.counter
        for i in range(self.num_players):
            state['player_{}_count'.format(i)] = self.counts[i]
        return state

    
class OvercookedGame(Game):
    """
    Class for bridging the gap between Overcooked_Env and the Game interface

    Instance variable:
        - max_players (int): Maximum number of players that can be in the game at once
        - mdp (OvercookedGridworld): Controls the underlying Overcooked game logic
        - score (int): Current reward acheived by all players
        - max_time (int): Number of seconds the game should last
        - npc_policies (dict): Maps user_id to policy (Agent) for each AI player
        - npc_state_queues (dict): Mapping of NPC user_ids to LIFO queues for the policy to process
        - curr_tick (int): How many times the game server has called this instance's `tick` method
        - ticker_per_ai_action (int): How many frames should pass in between NPC policy forward passes. 
            Note that this is a lower bound; if the policy is computationally expensive the actual frames
            per forward pass can be higher
        - action_to_overcooked_action (dict): Maps action names returned by client to action names used by OvercookedGridworld
            Note that this is an instance variable and not a static variable for efficiency reasons
        - trajectory (list(dict)): list of state-action pairs in current trajectory
    
    Methods:
        - npc_policy_consumer: Background process that asynchronously computes NPC policy forward passes. One thread
            spawned for each NPC
        - _curr_game_over: Determines whether the game on the current mdp has ended
    """

    def __init__(self, layouts=["cramped_room"], mdp_params={}, num_players=2, gameTime=30, playerZero='human', playerOne='human', psiturk_uid="-1", **kwargs):
        super(OvercookedGame, self).__init__(**kwargs)
        self.psiturk_uid = psiturk_uid
        self.mdp_params = mdp_params
        self.layouts = layouts
        self.max_players = int(num_players)
        self.mdp = None
        self.score = 0
        self.max_time = int(gameTime)
        self.npc_policies = {}
        self.npc_state_queues = {}
        self.action_to_overcooked_action = {
            "STAY" : Action.STAY,
            "UP" : Direction.NORTH,
            "DOWN" : Direction.SOUTH,
            "LEFT" : Direction.WEST,
            "RIGHT" : Direction.EAST,
            "SPACE" : Action.INTERACT
        }
        self.ticks_per_ai_action = 4
        self.curr_tick = 0
        self.trajectory = []

        if playerZero != 'human':
            player_zero_id = playerZero + '_0'
            self.add_player(player_zero_id, idx=0, buff_size=1)
            self.npc_policies[player_zero_id] = self.get_policy(playerZero)
            self.npc_state_queues[player_zero_id] = LifoQueue()

        if playerOne != 'human':
            player_one_id = playerOne + '_1'
            self.add_player(player_one_id, idx=1, buff_size=1)
            self.npc_policies[player_one_id] = self.get_policy(playerOne)
            self.npc_state_queues[player_one_id] = LifoQueue()

        if len(self.npc_policies) == self.max_players:
            raise ValueError("At least one player must be a human")

    def _curr_game_over(self):
        return time() - self.start_time >= self.max_time


    def needs_reset(self):
        return self._curr_game_over() and not self.is_finished()


    def npc_policy_consumer(self, policy_id):
        queue = self.npc_state_queues[policy_id]
        policy = self.npc_policies[policy_id]
        while self._is_active:
            state = queue.get()
            npc_action, _ = policy.action(state)
            self.enqueue_action(policy_id, npc_action)


    def is_full(self):
        return self.num_players >= self.max_players

    def is_finished(self):
        val = not self.layouts and self._curr_game_over()
        return val

    def apply_action(self, player_id, action):
        pass

    def apply_actions(self):
        # Default joint action, as NPC policies and clients probably don't enqueue actions fast 
        # enough to produce one at every tick
        joint_action = [Action.STAY] * len(self.players)

        # Synchronize individual player actions into a joint-action as required by overcooked logic
        for i in range(len(self.players)):
            try:
                joint_action[i] = self.pending_actions[i].get(block=False)
            except Empty:
                pass
        
        # Apply overcooked game logic to get state transition
        prev_state = self.state
        self.state, info = self.mdp.get_state_transition(prev_state, joint_action)

        # Send next state to all background consumers if needed
        if self.curr_tick % self.ticks_per_ai_action == 0:
            for npc_id in self.npc_policies:
                self.npc_state_queues[npc_id].put(self.state, block=False)

        # Update score based on soup deliveries that might have occured
        curr_reward = sum(info['sparse_reward_by_agent'])
        self.score += curr_reward

        # Log transition in our current trajectory
        transition = {
            "state" : prev_state.to_dict(),
            "joint_action" : joint_action,
            "reward" : curr_reward,
            "time_left" : max(self.max_time - (time() - self.start_time), 0),
            "score" : self.score,
            "time_elapsed" : time() - self.start_time,
            "cur_gameloop" : self.curr_tick,
            "layout" : self.mdp.terrain_mtx,
            "layout_name" : self.curr_layout,
            "trial_id" : self.trial_id
        }
        self.trajectory.append(transition)
        

    def enqueue_action(self, player_id, action):
        overcooked_action = self.action_to_overcooked_action[action]
        super(OvercookedGame, self).enqueue_action(player_id, overcooked_action)

    def reset(self):
        status = super(OvercookedGame, self).reset()
        if status == self.Status.RESET:
            # Hacky way of making sure game timer doesn't "start" until after reset timeout has passed
            self.start_time += self.reset_timeout / 1000


    def tick(self):
        self.curr_tick += 1
        return super(OvercookedGame, self).tick()

    def activate(self):
        super(OvercookedGame, self).activate()
        self.curr_layout = self.layouts.pop()
        self.mdp = OvercookedGridworld.from_layout_name(self.curr_layout, **self.mdp_params)
        self.state = self.mdp.get_standard_start_state()
        self.start_time = time()
        self.score = 0
        self.threads = []
        self.trial_id = self.psiturk_uid + str(self.start_time)
        for npc_policy in self.npc_policies:
            self.npc_policies[npc_policy].reset()
            self.npc_state_queues[npc_policy].put(self.state)
            t = Thread(target=self.npc_policy_consumer, args=(npc_policy,))
            self.threads.append(t)
            t.start()

    def deactivate(self):
        super(OvercookedGame, self).deactivate()
        # Ensure the background consumers do not hang
        for npc_policy in self.npc_policies:
            self.npc_state_queues[npc_policy].put(self.state)

        # Wait for all background threads to exit
        for t in self.threads:
            t.join()

        # Clear all action queues
        self.clear_pending_actions()


    def get_state(self):
        state_dict = {}
        state_dict['state'] = self.state.to_dict()
        state_dict['score'] = self.score
        state_dict['time_left'] = max(self.max_time - (time() - self.start_time), 0)
        return state_dict

    def to_json(self):
        obj_dict = {}
        obj_dict['terrain'] = self.mdp.terrain_mtx if self._is_active else None
        obj_dict['state'] = self.get_state() if self._is_active else None
        return obj_dict

    def get_policy(self, npc_id):
        fpath = os.path.join(AGENT_DIR, npc_id, 'agent.pickle')
        with open(fpath, 'rb') as f:
            return pickle.load(f)

    def get_data(self):
        """
        Returns and then clears the accumulated trajectory
        """
        data = { "uid" : self.psiturk_uid  + "_" + str(time()), "trajectory" : self.trajectory }
        self.trajectory = []
        return data


class OvercookedTutorial(OvercookedGame):
    

    def __init__(self, layouts=["tutorial_0"], mdp_params={}, playerZero='human', playerOne='AI', phaseTwoScore=15, **kwargs):
        super(OvercookedTutorial, self).__init__(layouts=layouts, mdp_params=mdp_params, playerZero=playerZero, playerOne=playerOne, **kwargs)
        self.phase_two_score = phaseTwoScore
        self.max_time = 0
        self.max_players = 2
        self.ticks_per_ai_action = 8
        self.curr_phase = 0

    @property
    def reset_timeout(self):
        return 1

    def needs_reset(self):
        if self.curr_phase == 0:
            return self.score > 0
        elif self.curr_phase == 1:
            return self.score > 0
        elif self.curr_phase == 2:
            return self.score > 0
        return False 

    def is_finished(self):
        return not self.layouts and self.score >= float('inf')

    def reset(self):
        super(OvercookedTutorial, self).reset()
        self.curr_phase += 1

    def get_policy(self, _):
        return TutorialAI()

    def apply_actions(self):
        # Default joint action, as NPC policies and clients probably don't enqueue actions fast 
        # enough to produce one at every tick
        joint_action = [Action.STAY] * len(self.players)

        # Synchronize individual player actions into a joint-action as required by overcooked logic
        for i in range(len(self.players)):
            try:
                joint_action[i] = self.pending_actions[i].get(block=False)
            except Empty:
                pass
        
        # Apply overcooked game logic to get state transition
        prev_state = self.state
        self.state, info = self.mdp.get_state_transition(prev_state, joint_action)

        # Send next state to all background consumers if needed
        if self.curr_tick % self.ticks_per_ai_action == 0:
            for npc_id in self.npc_policies:
                self.npc_state_queues[npc_id].put(self.state, block=False)

        # Update score based on soup deliveries of human agent only
        curr_reward = info['sparse_reward_by_agent'][0]

        # Phase two requires a specific reward to complete
        curr_reward = curr_reward if self.curr_phase != 2 or curr_reward == self.phase_two_score else 0
        self.score += curr_reward

        # Log transition in our current trajectory
        transition = {
            "state" : prev_state.to_dict(),
            "joint_action" : joint_action,
            "reward" : curr_reward,
            "time_left" : max(self.max_time - (time() - self.start_time), 0),
            "score" : self.score,
            "time_elapsed" : time() - self.start_time,
            "cur_gameloop" : self.curr_tick,
            "layout" : self.mdp.terrain_mtx,
            "layout_name" : self.curr_layout,
            "trial_id" : self.trial_id
        }
        self.trajectory.append(transition)




class DummyOvercookedGame(OvercookedGame):
    """
    Class that hardcodes the AI to be random. Used for debugging
    """
    
    def __init__(self, layouts=["cramped_room"], **kwargs):
        super(DummyOvercookedGame, self).__init__(layouts, **kwargs)

    def get_policy(self, _):
        return DummyAI()


class DummyAI():
    """
    Randomly samples actions. Used for debugging
    """
    def action(self, state):
        [action] = random.sample(['STAY', 'UP', 'DOWN', 'LEFT', 'RIGHT', 'SPACE'], 1)
        return action, None

    def reset(self):
        pass

class DummyComputeAI(DummyAI):
    """
    Performs simulated compute before randomly sampling actions. Used for debugging
    """
    def __init__(self, compute_unit_iters=1e5):
        """
        compute_unit_iters (int): Number of for loop cycles in one "unit" of compute. Number of 
                                    units performed each time is randomly sampled
        """
        super(DummyComputeAI, self).__init__()
        self.compute_unit_iters = int(compute_unit_iters)
    
    def action(self, state):
        # Randomly sample amount of time to busy wait
        iters = random.randint(1, 10) * self.compute_unit_iters

        # Actually compute something (can't sleep) to avoid scheduling optimizations
        val = 0
        for i in range(iters):
            # Avoid branch prediction optimizations
            if i % 2 == 0:
                val += 1
            else:
                val += 2
        
        # Return randomly sampled action
        return super(DummyComputeAI, self).action(state)

    
class StayAI():
    """
    Always returns "stay" action. Used for debugging
    """
    def action(self, state):
        return 'STAY', None

    def reset(self):
        pass


class TutorialAI():

    COOK_SOUP_LOOP = [
        # Grab first onion
        'LEFT',
        'LEFT',
        'LEFT',
        'SPACE',

        # Place onion in pot
        'RIGHT',
        'UP',
        'SPACE',

        # Grab second onion
        'LEFT',
        'SPACE',

        # Place onion in pot
        'RIGHT',
        'UP',
        'SPACE',

        # Grab third onion
        'LEFT',
        'SPACE',

        # Place onion in pot
        'RIGHT',
        'UP',
        'SPACE',

        # Cook soup
        'SPACE',
        
        # Grab plate
        'RIGHT',
        'DOWN',
        'SPACE',
        'LEFT',
        'UP',

        # Deliver soup
        'SPACE',
        'RIGHT',
        'RIGHT',
        'RIGHT',
        'SPACE',
        'LEFT'
    ]

    COOK_SOUP_COOP_LOOP = [
        # Grab first onion
        'LEFT',
        'LEFT',
        'LEFT',
        'SPACE',

        # Place onion in pot
        'RIGHT',
        'DOWN',
        'SPACE',

        # Move to start so this loops
        'RIGHT',
        'RIGHT',

        # Pause to make cooperation more real time
        'STAY',
        'STAY',
        'STAY',
        'STAY',
        'STAY',
        'STAY',
        'STAY',
        'STAY',
        'STAY'
    ]

    def __init__(self):
        self.curr_phase = -1
        self.curr_tick = -1

    def action(self, state):
        self.curr_tick += 1
        if self.curr_phase == 0:
            return self.COOK_SOUP_LOOP[self.curr_tick % len(self.COOK_SOUP_LOOP)], None
        elif self.curr_phase == 2:
            return self.COOK_SOUP_COOP_LOOP[self.curr_tick % len(self.COOK_SOUP_COOP_LOOP)], None
        return 'STAY', None

    def reset(self):
        self.curr_tick = -1
        self.curr_phase += 1

    