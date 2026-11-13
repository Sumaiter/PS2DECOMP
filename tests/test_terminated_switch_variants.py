from pathlib import Path
import sys,unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from search_rules import Variants
from terminated_switch_variants import children
ROOT=Path(__file__).parent / 'fixtures' / 'project'
class Tests(unittest.TestCase):
 def setUp(self):self.rules=Variants(ROOT)
 def generate(self,body):return children('int f(int x){int y=0;switch(x){'+body+'}return y;}',self.rules)
 def test_terminated_arms(self):
  got=self.generate('case 512:y|=512;break;case 6:y|=256;break;')
  self.assertEqual(len(got),1);self.assertLess(got[0][0].index('case 6:'),got[0][0].index('case 512:'))
 def test_preserves_default(self):
  got=self.generate('case 1:y=3;break;case 2:y=5;break;default:y=9;break;')
  self.assertEqual(len(got),1);self.assertIn('default:',got[0][0]);self.assertIn('y = 9;',got[0][0])
 def test_fallthrough_rejected(self):self.assertFalse(self.generate('case 1:y=3;case 2:y=5;break;'))
 def test_stacked_labels_rejected(self):self.assertFalse(self.generate('case 1:case 2:y=5;break;'))
 def test_declarations_rejected(self):self.assertFalse(self.generate('case 1:{int z=2;y=z;}break;case 2:y=5;break;'))
 def test_cross_arm_goto_rejected(self):self.assertFalse(self.generate('case 1:goto next;break;case 2:next:y=5;break;'))
 def test_conditional_break_is_not_terminator(self):self.assertFalse(self.generate('case 1:if(x)y=3;else break;case 2:y=5;break;'))
 def test_return_arms_preserve_expression(self):
  got=self.generate('case 1:return x+3;case 2:return x-5;')
  self.assertEqual(len(got),1);self.assertIn('return x + 3;',got[0][0]);self.assertIn('return x - 5;',got[0][0])
if __name__=='__main__':unittest.main()
