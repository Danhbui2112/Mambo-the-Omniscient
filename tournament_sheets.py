"""
Tournament Google Sheets Integration
Sync tournament data to Google Sheets and archive completed tournaments
"""

import gspread
from gspread.exceptions import WorksheetNotFound, APIError
from typing import Optional, List, Dict
from datetime import datetime
import os

# Use same credentials as main bot
SERVICE_ACCOUNT_FILE = 'credentials.json'
GOOGLE_SHEET_ID = os.getenv('GOOGLE_SHEET_ID')
TOURNAMENT_SHEET_NAME = 'Tournament'


class TournamentSheets:
    """Manages tournament data sync to Google Sheets"""
    
    def __init__(self):
        self.gc = None
        self.sh = None
        self.ws = None
        self.connected = False
        self._connect()
    
    def _connect(self):
        """Connect to Google Sheets"""
        try:
            self.gc = gspread.service_account(filename=SERVICE_ACCOUNT_FILE)
            self.sh = self.gc.open_by_key(GOOGLE_SHEET_ID)
            
            # Get or create Tournament sheet
            try:
                self.ws = self.sh.worksheet(TOURNAMENT_SHEET_NAME)
            except WorksheetNotFound:
                self.ws = self.sh.add_worksheet(title=TOURNAMENT_SHEET_NAME, rows=1000, cols=20)
                self._setup_headers()
            
            self.connected = True
            print("✅ Tournament Sheets connected")
        except Exception as e:
            print(f"❌ Tournament Sheets connection error: {e}")
            self.connected = False
    
    def _setup_headers(self):
        """Setup headers for tournament sheet"""
        headers = [
            'Tournament', 'Status', 'Player', 'Discord_ID', 
            'Match_ID', 'Round', 'Bans', 'Picks', 
            'Game1', 'Game2', 'Game3', 'Final_Place'
        ]
        self.ws.update('A1:L1', [headers])
    
    def sync_registrant(self, tournament_name: str, player_name: str, discord_id: int):
        """Add a registrant to the sheet"""
        if not self.connected:
            return False
        
        try:
            row = [
                tournament_name,
                'registered',
                player_name,
                str(discord_id),
                '', '', '', '', '', '', '', ''
            ]
            self.ws.append_row(row)
            return True
        except Exception as e:
            print(f"Error syncing registrant: {e}")
            return False
    
    def sync_match_data(self, tournament_name: str, match_id: str, round_num: int,
                        players: List[Dict], bans: Dict, picks: Dict):
        """Sync match ban/pick data"""
        if not self.connected:
            return False
        
        try:
            for player in players:
                player_id = player['id']
                row = [
                    tournament_name,
                    'in_match',
                    player['name'],
                    str(player_id),
                    match_id,
                    str(round_num),
                    ', '.join(bans.get(player_id, [])),
                    ', '.join(picks.get(player_id, [])),
                    '', '', '', ''
                ]
                
                # Find existing row for this player in this match
                cell = self.ws.find(str(player_id))
                if cell:
                    # Check if same match
                    row_data = self.ws.row_values(cell.row)
                    if len(row_data) > 4 and row_data[4] == match_id:
                        # Update existing row
                        self.ws.update(f'A{cell.row}:L{cell.row}', [row])
                        continue
                
                # Append new row
                self.ws.append_row(row)
            
            return True
        except Exception as e:
            print(f"Error syncing match data: {e}")
            return False
    
    def sync_game_result(self, tournament_name: str, match_id: str, 
                         game_num: int, placements: Dict[int, int]):
        """Sync a single game result"""
        if not self.connected:
            return False
        
        try:
            for player_id, position in placements.items():
                # Find player row for this match
                cells = self.ws.findall(str(player_id))
                for cell in cells:
                    row_data = self.ws.row_values(cell.row)
                    if len(row_data) > 4 and row_data[4] == match_id:
                        # Update game result column (Game1=col 9, Game2=col 10, Game3=col 11)
                        col = 8 + game_num  # 1-indexed
                        self.ws.update_cell(cell.row, col, str(position))
                        break
            
            return True
        except Exception as e:
            print(f"Error syncing game result: {e}")
            return False
    
    def sync_final_placement(self, tournament_name: str, player_id: int, final_place: int):
        """Sync final tournament placement"""
        if not self.connected:
            return False
        
        try:
            cells = self.ws.findall(str(player_id))
            for cell in cells:
                row_data = self.ws.row_values(cell.row)
                if len(row_data) > 0 and row_data[0] == tournament_name:
                    self.ws.update_cell(cell.row, 12, str(final_place))  # Final_Place column
                    self.ws.update_cell(cell.row, 2, 'completed')  # Status column
            
            return True
        except Exception as e:
            print(f"Error syncing final placement: {e}")
            return False
    
    def archive_tournament(self, tournament_name: str):
        """
        Archive tournament by:
        1. Adding archive header row
        2. Moving all current data down
        3. Marking tournament as archived
        """
        if not self.connected:
            return False
        
        try:
            # Get all data
            all_data = self.ws.get_all_values()
            if not all_data:
                return False
            
            headers = all_data[0]
            
            # Find rows for this tournament
            tournament_rows = []
            other_rows = []
            
            for row in all_data[1:]:  # Skip header
                if row and row[0] == tournament_name:
                    tournament_rows.append(row)
                else:
                    other_rows.append(row)
            
            if not tournament_rows:
                return False
            
            # Create archive header
            archive_time = datetime.now().strftime('%Y-%m-%d %H:%M')
            archive_header = [f'=== ARCHIVE: {tournament_name} ({archive_time}) ==='] + [''] * (len(headers) - 1)
            
            # Rebuild sheet: headers, blank space for new tournament, then archive
            new_data = [headers]
            new_data.append([''] * len(headers))  # Blank row for new data
            new_data.append(archive_header)
            new_data.extend(tournament_rows)
            
            # Add previous archives/other data
            if other_rows:
                new_data.append([''] * len(headers))  # Separator
                new_data.extend(other_rows)
            
            # Clear and rewrite
            self.ws.clear()
            self.ws.update(f'A1:L{len(new_data)}', new_data)
            
            print(f"✅ Archived tournament: {tournament_name}")
            return True
            
        except Exception as e:
            print(f"Error archiving tournament: {e}")
            return False
    
    def get_tournament_data(self, tournament_name: str) -> List[Dict]:
        """Get all data for a tournament"""
        if not self.connected:
            return []
        
        try:
            all_data = self.ws.get_all_records()
            return [row for row in all_data if row.get('Tournament') == tournament_name]
        except Exception as e:
            print(f"Error getting tournament data: {e}")
            return []


# Singleton instance
_sheets_instance: Optional[TournamentSheets] = None

def get_tournament_sheets() -> TournamentSheets:
    """Get or create sheets instance"""
    global _sheets_instance
    if _sheets_instance is None:
        _sheets_instance = TournamentSheets()
    return _sheets_instance
