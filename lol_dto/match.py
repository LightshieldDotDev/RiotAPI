"""Tables related to Match Data."""
import asyncio

from sqlalchemy import Column, Boolean, String, BigInteger, SmallInteger, VARCHAR

from . import Base, Team, Player, Runes


class Match(Base):
    """Match Base Element.

    Aims to resemble the LongtermStructure format.
    """
    __tablename__ = 'match'

    matchId = Column(BigInteger, primary_key=True)
    status = Column(VARCHAR(1), index=True)

    start = Column(BigInteger)
    duration = Column(SmallInteger)
    gameVersion = Column(String)

    win = Column(Boolean)  # False: Blue | True: Red

    @classmethod
    async def create(cls, match):
        """Create the match object as well as sub elements"""
        matchObject = cls(
            start=match['gameCreation'] // 1000,
            duration=match['gameDuration'],
            gameVersion=match['gameVersion'],
            matchId=match['gameId'],
        )
        matchObject.win = match['teams'][0]['win'] == 'Win'
        objects = [matchObject]

        objects += [await Team.create(match, 0), await Team.create(match, 1)] + await asyncio.gather(*[
            asyncio.create_task(Player.create(match, i)) for i in range(1, 11)
        ])
        for entry in await asyncio.gather(*[
            asyncio.create_task(Runes.create(match, i)) for i in range(1, 11)
        ]):
            objects += entry

        return objects
