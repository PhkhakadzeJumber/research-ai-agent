"""Repositories: the read/write functions between Eureka's logic and the tables.

Agents and workflows never write SQL themselves; they call these functions, so all
database rules live in one tested place. Functions take an AsyncSession and never
commit: the caller decides the unit of work (Database.session() commits at the end).
"""
