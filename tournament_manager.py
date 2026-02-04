"""
Tournament Manager - Core logic và data models cho Tournament 1v1v1
"""
import json
import os
import uuid
import random
from dataclasses import dataclass, field, asdict
from typing import List, Dict, Optional
from datetime import datetime
import asyncio

SCRIPT_DIR = os.path.dirname(os.path.realpath(__file__))
TOURNAMENTS_DIR = os.path.join(SCRIPT_DIR, "tournaments")
os.makedirs(TOURNAMENTS_DIR, exist_ok=True)


@dataclass
class TournamentPlayer:
    """Người chơi trong tournament"""
    discord_id: int
    display_name: str
    joined_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    
    def to_dict(self):
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data: dict):
        return cls(**data)


@dataclass
class Match:
    """Một trận đấu 1v1v1"""
    match_id: str
    round_num: int
    players: List[int]  # 3 discord_ids
    thread_id: Optional[int] = None
    role_id: Optional[int] = None
    
    # Ban phase
    bans: Dict[int, List[str]] = field(default_factory=dict)  # {player_id: [uma_names]}
    current_ban_turn: int = 0  # Index trong players
    ban_complete: bool = False
    
    # Pick phase
    picks: Dict[int, List[str]] = field(default_factory=dict)  # {player_id: [uma_names]}
    current_pick_turn: int = 0
    pick_complete: bool = False
    
    # BO3 results
    game_results: List[Dict] = field(default_factory=list)  # [{winner: id, placements: [1,2,3]}]
    match_winner: Optional[int] = None
    
    status: str = "pending"  # pending, banning, picking, playing, completed
    
    def get_all_banned_uma(self) -> List[str]:
        """Lấy tất cả uma đã bị ban"""
        all_bans = []
        for uma_list in self.bans.values():
            all_bans.extend(uma_list)
        return all_bans
    
    def get_current_ban_player(self) -> Optional[int]:
        """Lấy player đang có lượt ban"""
        if self.ban_complete or self.current_ban_turn >= len(self.players):
            return None
        return self.players[self.current_ban_turn]
    
    def get_current_pick_player(self) -> Optional[int]:
        """Lấy player đang có lượt pick"""
        if self.pick_complete or self.current_pick_turn >= len(self.players):
            return None
        return self.players[self.current_pick_turn]
    
    def add_bans(self, player_id: int, uma_names: List[str]) -> bool:
        """Thêm bans cho player"""
        if player_id != self.get_current_ban_player():
            return False
        
        self.bans[player_id] = uma_names
        self.current_ban_turn += 1
        
        if self.current_ban_turn >= len(self.players):
            self.ban_complete = True
            self.status = "picking"
        
        return True
    
    def add_picks(self, player_id: int, uma_names: List[str]) -> bool:
        """Thêm picks cho player"""
        if player_id != self.get_current_pick_player():
            return False
        
        self.picks[player_id] = uma_names
        self.current_pick_turn += 1
        
        if self.current_pick_turn >= len(self.players):
            self.pick_complete = True
            self.status = "playing"
        
        return True
    
    def add_game_result(self, placements: Dict[int, int]) -> Optional[int]:
        """
        Thêm kết quả game
        placements: {player_id: position} (1, 2, 3)
        Returns match_winner if BO3 complete
        """
        self.game_results.append(placements)
        
        # Check BO3 winner
        if len(self.game_results) >= 2:
            wins = {}
            for result in self.game_results:
                for player_id, position in result.items():
                    if position == 1:
                        wins[player_id] = wins.get(player_id, 0) + 1
            
            for player_id, win_count in wins.items():
                if win_count >= 2:
                    self.match_winner = player_id
                    self.status = "completed"
                    return player_id
        
        return None
    
    def to_dict(self):
        return {
            'match_id': self.match_id,
            'round_num': self.round_num,
            'players': self.players,
            'thread_id': self.thread_id,
            'role_id': self.role_id,
            'bans': {str(k): v for k, v in self.bans.items()},
            'current_ban_turn': self.current_ban_turn,
            'ban_complete': self.ban_complete,
            'picks': {str(k): v for k, v in self.picks.items()},
            'current_pick_turn': self.current_pick_turn,
            'pick_complete': self.pick_complete,
            'game_results': self.game_results,
            'match_winner': self.match_winner,
            'status': self.status
        }
    
    @classmethod
    def from_dict(cls, data: dict):
        match = cls(
            match_id=data['match_id'],
            round_num=data['round_num'],
            players=data['players'],
            thread_id=data.get('thread_id'),
            role_id=data.get('role_id'),
            current_ban_turn=data.get('current_ban_turn', 0),
            ban_complete=data.get('ban_complete', False),
            current_pick_turn=data.get('current_pick_turn', 0),
            pick_complete=data.get('pick_complete', False),
            game_results=data.get('game_results', []),
            match_winner=data.get('match_winner'),
            status=data.get('status', 'pending')
        )
        match.bans = {int(k): v for k, v in data.get('bans', {}).items()}
        match.picks = {int(k): v for k, v in data.get('picks', {}).items()}
        return match


@dataclass
class Tournament:
    """Tournament chính"""
    id: str
    name: str
    guild_id: int
    created_by: int
    
    # Channel/Role IDs
    admin_channel_id: Optional[int] = None
    public_channel_id: Optional[int] = None  # announce channel
    chat_channel_id: Optional[int] = None
    forum_channel_id: Optional[int] = None
    category_id: Optional[int] = None
    tournament_role_id: Optional[int] = None
    referee_role_id: Optional[int] = None
    registration_message_id: Optional[int] = None
    
    # Settings
    max_players: int = 12
    bans_per_player: int = 3
    picks_per_player: int = 3
    prize_pool: Optional[str] = None  # Optional prize pool text
    
    # State
    status: str = "setup"  # setup, registration, in_progress, completed
    players: Dict[int, TournamentPlayer] = field(default_factory=dict)
    matches: List[Match] = field(default_factory=list)
    current_round: int = 0
    
    created_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    
    def add_player(self, discord_id: int, display_name: str) -> bool:
        """Thêm player vào tournament"""
        if discord_id in self.players:
            return False
        if len(self.players) >= self.max_players:
            return False
        
        self.players[discord_id] = TournamentPlayer(
            discord_id=discord_id,
            display_name=display_name
        )
        return True
    
    def remove_player(self, discord_id: int) -> bool:
        """Xóa player khỏi tournament"""
        if discord_id not in self.players:
            return False
        del self.players[discord_id]
        return True
    
    def generate_bracket(self) -> List[Match]:
        """
        Generate random bracket cho Swiss format
        Mỗi match 3 người
        """
        player_ids = list(self.players.keys())
        random.shuffle(player_ids)
        
        matches = []
        match_num = 1
        
        # Chia thành các group 3 người
        for i in range(0, len(player_ids), 3):
            group = player_ids[i:i+3]
            
            # Nếu group < 3, cần xử lý bye
            if len(group) < 3:
                # TODO: Handle byes
                continue
            
            match = Match(
                match_id=f"R{self.current_round + 1}M{match_num}",
                round_num=self.current_round + 1,
                players=group,
                status="banning"
            )
            matches.append(match)
            match_num += 1
        
        self.matches.extend(matches)
        self.current_round += 1
        return matches
    
    def get_match_by_thread(self, thread_id: int) -> Optional[Match]:
        """Tìm match theo thread ID"""
        for match in self.matches:
            if match.thread_id == thread_id:
                return match
        return None
    
    def get_active_matches(self) -> List[Match]:
        """Lấy các match đang active"""
        return [m for m in self.matches if m.status not in ['completed', 'pending']]
    
    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'guild_id': self.guild_id,
            'created_by': self.created_by,
            'admin_channel_id': self.admin_channel_id,
            'public_channel_id': self.public_channel_id,
            'chat_channel_id': self.chat_channel_id,
            'forum_channel_id': self.forum_channel_id,
            'category_id': self.category_id,
            'tournament_role_id': self.tournament_role_id,
            'referee_role_id': self.referee_role_id,
            'registration_message_id': self.registration_message_id,
            'max_players': self.max_players,
            'bans_per_player': self.bans_per_player,
            'picks_per_player': self.picks_per_player,
            'prize_pool': self.prize_pool,
            'status': self.status,
            'players': {str(k): v.to_dict() for k, v in self.players.items()},
            'matches': [m.to_dict() for m in self.matches],
            'current_round': self.current_round,
            'created_at': self.created_at
        }
    
    @classmethod
    def from_dict(cls, data: dict):
        tournament = cls(
            id=data['id'],
            name=data['name'],
            guild_id=data['guild_id'],
            created_by=data['created_by'],
            admin_channel_id=data.get('admin_channel_id'),
            public_channel_id=data.get('public_channel_id'),
            chat_channel_id=data.get('chat_channel_id'),
            forum_channel_id=data.get('forum_channel_id'),
            category_id=data.get('category_id'),
            tournament_role_id=data.get('tournament_role_id'),
            referee_role_id=data.get('referee_role_id'),
            registration_message_id=data.get('registration_message_id'),
            max_players=data.get('max_players', 12),
            bans_per_player=data.get('bans_per_player', 3),
            picks_per_player=data.get('picks_per_player', 3),
            prize_pool=data.get('prize_pool'),
            status=data.get('status', 'setup'),
            current_round=data.get('current_round', 0),
            created_at=data.get('created_at', datetime.utcnow().isoformat())
        )
        
        # Load players
        for player_id, player_data in data.get('players', {}).items():
            tournament.players[int(player_id)] = TournamentPlayer.from_dict(player_data)
        
        # Load matches
        for match_data in data.get('matches', []):
            tournament.matches.append(Match.from_dict(match_data))
        
        return tournament
    
    def save(self):
        """Lưu tournament vào file"""
        filepath = os.path.join(TOURNAMENTS_DIR, f"{self.id}.json")
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(self.to_dict(), f, indent=2, ensure_ascii=False)
    
    @classmethod
    def load(cls, tournament_id: str) -> Optional['Tournament']:
        """Load tournament từ file"""
        filepath = os.path.join(TOURNAMENTS_DIR, f"{tournament_id}.json")
        if not os.path.exists(filepath):
            return None
        
        with open(filepath, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return cls.from_dict(data)


# Global active tournament per guild
_active_tournaments: Dict[int, Tournament] = {}


def get_active_tournament(guild_id: int) -> Optional[Tournament]:
    """Lấy tournament đang active của guild"""
    return _active_tournaments.get(guild_id)


def set_active_tournament(guild_id: int, tournament: Tournament):
    """Set tournament active cho guild"""
    _active_tournaments[guild_id] = tournament


def create_tournament(guild_id: int, name: str, created_by: int) -> Tournament:
    """Tạo tournament mới"""
    tournament_id = str(uuid.uuid4())[:8]
    
    tournament = Tournament(
        id=tournament_id,
        name=name,
        guild_id=guild_id,
        created_by=created_by
    )
    
    set_active_tournament(guild_id, tournament)
    tournament.save()
    
    return tournament


def load_active_tournaments():
    """Load tất cả tournaments từ disk khi bot start"""
    if not os.path.exists(TOURNAMENTS_DIR):
        return
    
    for filename in os.listdir(TOURNAMENTS_DIR):
        if filename.endswith('.json'):
            tournament_id = filename[:-5]
            tournament = Tournament.load(tournament_id)
            if tournament and tournament.status not in ['completed']:
                _active_tournaments[tournament.guild_id] = tournament
                print(f"✅ Loaded tournament: {tournament.name} ({tournament.id})")
