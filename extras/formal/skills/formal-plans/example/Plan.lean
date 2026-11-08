/-! A worked formal model of one hypothesis: "the new loss beats the baseline on dev". -/

namespace H1

/-- What the plan reasons about. Rates are in basis points, so derivations stay in `Nat`. -/
structure World where
  samples : Nat
  width_bp : Nat
  baseline_bp : Nat

/-- What must be true for the plan to work. Each field is linked to a citation, or marked assumed. -/
structure Premises (w : World) : Prop where
  width_shrinks : w.width_bp * w.samples ≤ 200000
  baseline_is : w.baseline_bp = 5200
  enough : 400 ≤ w.samples

/-- Some world satisfying the premises: they don't contradict each other. -/
def model : World := { samples := 400, width_bp := 500, baseline_bp := 5200 }

theorem model_ok : Premises model := ⟨by decide, by decide, by decide⟩

/-- The parameter choice, derived: 400 samples are enough for a half-width of at most 500 bp. -/
theorem design (w : World) (p : Premises w) : w.width_bp ≤ 500 := by
  have h := p.width_shrinks
  have hn := p.enough
  by_cases c : w.width_bp ≤ 500
  · exact c
  · exfalso
    have : 501 * 400 ≤ w.width_bp * w.samples := Nat.mul_le_mul (by omega) hn
    omega

/-- What a run reports. -/
structure Measured where
  acc : Rat
  samples : Nat

/-- The success condition, part by part, so a failure names its part. -/
def checks : List (String × (Measured → Bool)) :=
  [("beats the baseline by the half-width", fun m => decide (m.acc ≥ (57 : Rat) / 100)),
   ("used enough samples", fun m => decide (m.samples ≥ 400))]

def passing : Measured := { acc := (60 : Rat) / 100, samples := 500 }
def failing : Measured := { acc := (55 : Rat) / 100, samples := 500 }

end H1
